//! Deterministic scan primitives for BlockProbe, exposed through a narrow C ABI.
//!
//! Verdict aggregation and sinkhole classification are pure. The resolver
//! entry point calls the operating-system resolver synchronously; Python calls
//! it only from the existing worker thread. The Python/Flet application owns
//! evidence assembly, consent, networking policy, and user-facing behavior.

use regex::{RegexSet, RegexSetBuilder};
use std::ffi::{CStr, c_char, c_void};
use std::net::{IpAddr, ToSocketAddrs};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::ptr;
use std::slice;
use std::str;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    Blocked,
    Unreachable,
    Opened,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Confidence {
    Low,
    Medium,
    High,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReasonCode {
    DnsSinkhole,
    DnsFailure,
    DnsFiltered,
    ConnectRefused,
    ConnectReset,
    ConnectTimeout,
    TlsFailure,
    ConnectFailure,
    RegulatorRedirect,
    BlockSignature,
    BlockerPage,
    BlockerRedirect,
    IspBlackout,
    ChallengePage,
    PositiveMarkers,
    DeadPage,
    EmptyPage,
    HttpError,
    Unclassified,
    NetworkDown,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Classification {
    pub verdict: Verdict,
    pub reason_code: ReasonCode,
    pub confidence: Confidence,
    pub blocker_hint: Option<String>,
    pub needs_review: bool,
    pub rule_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UncertainOpenedVariant {
    pub reason_code: ReasonCode,
    pub confidence: Confidence,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Aggregate {
    pub classification: Classification,
    pub chosen_index: usize,
    /// Uncertain opened results that conflicted with retained block evidence.
    pub uncertain_opened_variants: Vec<UncertainOpenedVariant>,
}

fn is_confident_opened(item: &Classification) -> bool {
    item.verdict == Verdict::Opened
        && !item.needs_review
        && !matches!(
            item.reason_code,
            ReasonCode::EmptyPage | ReasonCode::Unclassified
        )
}

fn verdict_rank(verdict: Verdict) -> u8 {
    match verdict {
        Verdict::Opened => 3,
        Verdict::Blocked => 2,
        Verdict::Unreachable => 1,
    }
}

fn first_best<'a, K: Ord>(
    items: impl IntoIterator<Item = &'a Classification>,
    score: impl Fn(&Classification) -> K,
) -> Option<&'a Classification> {
    let mut best: Option<&Classification> = None;
    for candidate in items {
        if best.is_none_or(|current| score(candidate) > score(current)) {
            best = Some(candidate);
        }
    }
    best
}

/// Combine per-variant results while preserving verified block evidence.
///
/// Only confident opened evidence overrides a block. If opened variants are
/// uncertain and a block exists, the block is retained and the aggregate is
/// marked for review. Ties preserve the first item, matching the Python
/// reference's stable ordering.
pub fn aggregate(classifications: &[Classification]) -> Aggregate {
    if classifications.is_empty() {
        return Aggregate {
            classification: Classification {
                verdict: Verdict::Unreachable,
                reason_code: ReasonCode::Unclassified,
                confidence: Confidence::Low,
                blocker_hint: None,
                needs_review: true,
                rule_id: None,
            },
            chosen_index: 0,
            uncertain_opened_variants: Vec::new(),
        };
    }

    let opened = classifications
        .iter()
        .filter(|item| item.verdict == Verdict::Opened);
    let confident_opened: Vec<_> = opened
        .clone()
        .filter(|item| is_confident_opened(item))
        .collect();
    let uncertain_opened: Vec<_> = opened.filter(|item| !is_confident_opened(item)).collect();
    let blocked: Vec<_> = classifications
        .iter()
        .filter(|item| item.verdict == Verdict::Blocked)
        .collect();

    let chosen = first_best(confident_opened, |item| item.confidence)
        .or_else(|| {
            first_best(blocked.iter().copied(), |item| {
                (item.confidence, item.blocker_hint.is_some())
            })
        })
        .or_else(|| first_best(uncertain_opened.iter().copied(), |item| item.confidence))
        .or_else(|| {
            first_best(classifications, |item| {
                (verdict_rank(item.verdict), item.confidence)
            })
        })
        .expect("non-empty classifications have a candidate");

    let chosen_index = classifications
        .iter()
        .position(|item| std::ptr::eq(item, chosen))
        .expect("chosen classification comes from the input");
    let conflict = chosen.verdict == Verdict::Blocked && !uncertain_opened.is_empty();
    let mut classification = chosen.clone();
    classification.needs_review |= conflict;

    Aggregate {
        classification,
        chosen_index,
        uncertain_opened_variants: if conflict {
            uncertain_opened
                .into_iter()
                .map(|item| UncertainOpenedVariant {
                    reason_code: item.reason_code,
                    confidence: item.confidence,
                })
                .collect()
        } else {
            Vec::new()
        },
    }
}

/// Compact fixed-layout input shared with the Python ctypes adapter.
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct AggregateInput {
    /// 0 = blocked, 1 = unreachable, 2 = opened.
    pub verdict: u8,
    /// 0 = low, 1 = medium, 2 = high.
    pub confidence: u8,
    pub blocker_hint: u8,
    pub confident_opened: u8,
}

/// Return the version of the C ABI implemented by this native library.
#[unsafe(no_mangle)]
pub extern "C" fn bp_abi_version() -> u32 {
    2
}

/// Opaque compiled regex set shared with the Python response classifier.
struct PatternSetHandle(RegexSet);

/// Compile Python-owned response marker patterns with matching flags.
///
/// The caller retains the returned pointer for the process lifetime. A null
/// result means Rust could not represent this pattern set and Python should
/// use its reference matcher instead.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn bp_regex_set_new(
    patterns: *const *const c_char,
    length: usize,
) -> *mut c_void {
    catch_unwind(AssertUnwindSafe(|| {
        if patterns.is_null() || length == 0 {
            return ptr::null_mut();
        }
        // SAFETY: the C caller supplies `length` pointers to NUL-terminated
        // UTF-8 pattern strings for the duration of this call.
        let pattern_ptrs = unsafe { slice::from_raw_parts(patterns, length) };
        let mut owned = Vec::with_capacity(length);
        for pattern_ptr in pattern_ptrs {
            if pattern_ptr.is_null() {
                return ptr::null_mut();
            }
            // SAFETY: each pointer is checked and remains valid for this call.
            let Ok(pattern) = unsafe { CStr::from_ptr(*pattern_ptr) }.to_str() else {
                return ptr::null_mut();
            };
            owned.push(pattern.to_owned());
        }
        let Ok(set) = RegexSetBuilder::new(owned)
            .case_insensitive(true)
            .dot_matches_new_line(true)
            .build()
        else {
            return ptr::null_mut();
        };
        Box::into_raw(Box::new(PatternSetHandle(set))).cast()
    }))
    .unwrap_or(ptr::null_mut())
}

/// Return every matching regex index in ascending pattern order.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn bp_regex_set_matches(
    handle: *const c_void,
    text: *const u8,
    text_length: usize,
    output: *mut usize,
    capacity: usize,
) -> isize {
    ffi_guard(|| {
        if handle.is_null() || text.is_null() || output.is_null() {
            return -1;
        }
        // SAFETY: the caller passes a valid text byte slice and compiled
        // handle returned from `bp_regex_set_new`.
        let text_bytes = unsafe { slice::from_raw_parts(text, text_length) };
        let Ok(text) = str::from_utf8(text_bytes) else {
            return -1;
        };
        // SAFETY: the handle is created and retained by `bp_regex_set_new`.
        let handle = unsafe { &*handle.cast::<PatternSetHandle>() };
        let matches = handle.0.matches(text);
        let count = matches.iter().count();
        if count > capacity {
            return -1;
        }
        for (slot, pattern_index) in matches.iter().enumerate() {
            // SAFETY: `count <= capacity` and the caller allocated that many
            // writable `usize` values.
            unsafe { *output.add(slot) = pattern_index };
        }
        count as isize
    })
}

/// C ABI entry point used by Python to select one aggregate result.
/// Returns the selected index and stores a one-byte conflict flag.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn bp_aggregate(
    input: *const AggregateInput,
    length: usize,
    conflict: *mut u8,
) -> isize {
    ffi_guard(|| {
        if input.is_null() || conflict.is_null() || length == 0 {
            return -1;
        }
        // SAFETY: the caller provides `length` initialized C-layout rows.
        let rows = unsafe { std::slice::from_raw_parts(input, length) };
        if rows.iter().any(|row| {
            row.verdict > 2
                || row.confidence > 2
                || row.blocker_hint > 1
                || row.confident_opened > 1
        }) {
            return -1;
        }
        let chosen = aggregate_input_index(rows);
        let has_uncertain_open = rows
            .iter()
            .any(|row| row.verdict == 2 && row.confident_opened == 0);
        // SAFETY: conflict is non-null, checked above, and points to one writable byte.
        unsafe { *conflict = u8::from(rows[chosen].verdict == 0 && has_uncertain_open) };
        chosen as isize
    })
}

fn aggregate_input_index(rows: &[AggregateInput]) -> usize {
    let has_confident_open = rows
        .iter()
        .any(|row| row.verdict == 2 && row.confident_opened == 1);
    let has_blocked = rows.iter().any(|row| row.verdict == 0);
    let has_open = rows.iter().any(|row| row.verdict == 2);
    let eligible = |row: &AggregateInput| {
        if has_confident_open {
            row.verdict == 2 && row.confident_opened == 1
        } else if has_blocked {
            row.verdict == 0
        } else if has_open {
            row.verdict == 2
        } else {
            true
        }
    };
    let mut best_index = 0;
    let mut best_score = (0_u8, 0_u8, 0_u8);
    let mut found = false;
    for (index, row) in rows.iter().enumerate() {
        if !eligible(row) {
            continue;
        }
        let score = (row.confidence, row.blocker_hint, 0_u8);
        if !found || score > best_score {
            best_index = index;
            best_score = score;
            found = true;
        }
    }
    best_index
}

/// Resolve a hostname with the operating system resolver. It is deliberately
/// synchronous; Python calls it from the existing DNS worker thread.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn bp_system_resolve(
    host: *const c_char,
    output: *mut c_char,
    capacity: usize,
) -> isize {
    ffi_guard(|| {
        if host.is_null() || output.is_null() || capacity == 0 {
            return -1;
        }
        // SAFETY: callers provide a valid NUL-terminated hostname.
        let Ok(host) = unsafe { CStr::from_ptr(host) }.to_str() else {
            return -1;
        };
        let Ok(addresses) = (host, 0).to_socket_addrs() else {
            return -1;
        };
        let mut ips = Vec::<String>::new();
        for address in addresses {
            let ip = address.ip().to_string();
            if !ips.contains(&ip) {
                ips.push(ip);
            }
        }
        if ips.is_empty() {
            return -1;
        }
        write_output(output, capacity, &ips.join("\n"))
    })
}

/// Classify one resolved IP using the Rust engine's address policy.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn bp_is_sinkhole_ip(value: *const c_char) -> isize {
    ffi_guard(|| {
        if value.is_null() {
            return 0;
        }
        // SAFETY: callers provide a valid NUL-terminated address.
        let Ok(value) = unsafe { CStr::from_ptr(value) }.to_str() else {
            return 0;
        };
        isize::from(is_sinkhole_ip(value))
    })
}

fn is_sinkhole_ip(value: &str) -> bool {
    let Ok(address) = value.parse::<IpAddr>() else {
        return false;
    };
    match address {
        IpAddr::V4(ip) => {
            let [a, b, c, _] = ip.octets();
            ip.is_unspecified()
                || ip.is_loopback()
                || ip.is_link_local()
                || ip.is_private()
                || ip.is_broadcast()
                || ip.is_multicast()
                || a == 0
                || a >= 240
                || (a == 192 && b == 0 && c == 0)
                || (a == 192 && b == 0 && c == 2)
                || (a == 198 && (b == 18 || b == 19 || b == 51))
                || (a == 203 && b == 0 && c == 113)
        }
        IpAddr::V6(ip) => {
            let octets = ip.octets();
            ip.is_unspecified()
                || ip.is_loopback()
                || ip.is_unicast_link_local()
                || ip.is_multicast()
                || (octets[0] & 0xfe) == 0xfc
                || (octets[0] == 0x20
                    && octets[1] == 0x01
                    && octets[2] == 0x0d
                    && octets[3] == 0xb8)
                || ip
                    .to_ipv4_mapped()
                    .is_some_and(|mapped| is_sinkhole_ip(&mapped.to_string()))
        }
    }
}

fn write_output(output: *mut c_char, capacity: usize, value: &str) -> isize {
    if value.len() + 1 > capacity {
        return -1;
    }
    // SAFETY: caller supplied an output buffer of `capacity` bytes.
    unsafe {
        ptr::copy_nonoverlapping(value.as_ptr(), output.cast::<u8>(), value.len());
        *output.add(value.len()) = 0;
    }
    value.len() as isize
}

fn ffi_guard(operation: impl FnOnce() -> isize) -> isize {
    catch_unwind(AssertUnwindSafe(operation)).unwrap_or(-1)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn result(
        verdict: Verdict,
        reason_code: ReasonCode,
        confidence: Confidence,
        needs_review: bool,
        rule_id: Option<&str>,
    ) -> Classification {
        Classification {
            verdict,
            reason_code,
            confidence,
            blocker_hint: None,
            needs_review,
            rule_id: rule_id.map(str::to_owned),
        }
    }

    #[test]
    fn uncertain_opened_keeps_verified_block_and_marks_review() {
        let blocked = result(
            Verdict::Blocked,
            ReasonCode::BlockSignature,
            Confidence::High,
            false,
            Some("betblocker-blocked-by"),
        );
        let opened = result(
            Verdict::Opened,
            ReasonCode::Unclassified,
            Confidence::Low,
            true,
            None,
        );

        let result = aggregate(&[blocked, opened]);

        assert_eq!(result.classification.verdict, Verdict::Blocked);
        assert_eq!(
            result.classification.rule_id.as_deref(),
            Some("betblocker-blocked-by")
        );
        assert!(result.classification.needs_review);
        assert_eq!(
            result.uncertain_opened_variants,
            vec![UncertainOpenedVariant {
                reason_code: ReasonCode::Unclassified,
                confidence: Confidence::Low,
            }]
        );
    }

    #[test]
    fn confident_positive_opened_still_overrides_block() {
        let result = aggregate(&[
            result(
                Verdict::Blocked,
                ReasonCode::BlockSignature,
                Confidence::High,
                false,
                Some("block-rule"),
            ),
            result(
                Verdict::Opened,
                ReasonCode::PositiveMarkers,
                Confidence::High,
                false,
                None,
            ),
        ]);

        assert_eq!(result.classification.verdict, Verdict::Opened);
        assert_eq!(
            result.classification.reason_code,
            ReasonCode::PositiveMarkers
        );
        assert!(!result.classification.needs_review);
        assert!(result.uncertain_opened_variants.is_empty());
    }

    #[test]
    fn challenge_page_still_overrides_block() {
        let result = aggregate(&[
            result(
                Verdict::Blocked,
                ReasonCode::BlockSignature,
                Confidence::High,
                false,
                None,
            ),
            result(
                Verdict::Opened,
                ReasonCode::ChallengePage,
                Confidence::Medium,
                false,
                None,
            ),
        ]);

        assert_eq!(result.classification.verdict, Verdict::Opened);
        assert_eq!(result.classification.reason_code, ReasonCode::ChallengePage);
    }

    #[test]
    fn all_uncertain_opened_results_keep_opened_verdict() {
        let result = aggregate(&[
            result(
                Verdict::Opened,
                ReasonCode::Unclassified,
                Confidence::Low,
                true,
                None,
            ),
            result(
                Verdict::Opened,
                ReasonCode::EmptyPage,
                Confidence::Low,
                true,
                None,
            ),
        ]);

        assert_eq!(result.classification.verdict, Verdict::Opened);
        assert!(result.classification.needs_review);
        assert!(result.uncertain_opened_variants.is_empty());
    }

    #[test]
    fn empty_input_is_unreachable_and_needs_review() {
        let result = aggregate(&[]);

        assert_eq!(result.classification.verdict, Verdict::Unreachable);
        assert_eq!(result.classification.reason_code, ReasonCode::Unclassified);
        assert_eq!(result.classification.confidence, Confidence::Low);
        assert!(result.classification.needs_review);
    }

    #[test]
    fn rust_sinkhole_address_ranges_are_classified() {
        for ip in [
            "0.0.0.0",
            "127.0.0.1",
            "169.254.2.1",
            "10.0.0.1",
            "192.168.1.1",
            "224.0.0.1",
            "203.0.113.8",
            "::",
            "::1",
            "fe80::1",
            "fc00::1",
            "::ffff:192.168.1.1",
        ] {
            assert!(is_sinkhole_ip(ip), "{ip} should be a sinkhole address");
        }
        for ip in ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"] {
            assert!(!is_sinkhole_ip(ip), "{ip} should be public");
        }
        assert!(!is_sinkhole_ip("not-an-ip"));
    }

    #[test]
    fn c_abi_aggregation_roundtrips_selection_and_conflict() {
        let input = [
            AggregateInput {
                verdict: 0,
                confidence: 2,
                blocker_hint: 1,
                confident_opened: 0,
            },
            AggregateInput {
                verdict: 2,
                confidence: 0,
                blocker_hint: 0,
                confident_opened: 0,
            },
        ];
        let mut conflict = 0;
        // SAFETY: input is initialized and conflict points to one writable byte.
        let selected = unsafe { bp_aggregate(input.as_ptr(), input.len(), &mut conflict) };
        assert_eq!(selected, 0);
        assert_eq!(conflict, 1);
    }

    #[test]
    fn c_abi_regex_set_returns_matches_in_pattern_order() {
        let first = std::ffi::CString::new(r"\bfirst\s+marker\b").unwrap();
        let second = std::ffi::CString::new(r"other.*line").unwrap();
        let patterns = [first.as_ptr(), second.as_ptr()];
        // SAFETY: the pattern C strings stay alive through construction.
        let handle = unsafe { bp_regex_set_new(patterns.as_ptr(), patterns.len()) };
        assert!(!handle.is_null());

        let text = "First\nMarker and OTHER\nLINE".as_bytes();
        let mut indices = [usize::MAX; 2];
        // SAFETY: `handle` came from the constructor and the text/output
        // slices remain alive and writable for this call.
        let count = unsafe {
            bp_regex_set_matches(
                handle,
                text.as_ptr(),
                text.len(),
                indices.as_mut_ptr(),
                indices.len(),
            )
        };
        assert_eq!(count, 2);
        assert_eq!(indices, [0, 1]);

        // SAFETY: this test owns the handle returned above and drops it once.
        unsafe { drop(Box::from_raw(handle.cast::<PatternSetHandle>())) };
    }

    #[test]
    fn c_abi_regex_set_rejects_python_only_lookaround_for_fallback() {
        let unsupported = std::ffi::CString::new(r"(?=blocked)").unwrap();
        let patterns = [unsupported.as_ptr()];
        // SAFETY: the pattern C string stays alive through construction.
        let handle = unsafe { bp_regex_set_new(patterns.as_ptr(), patterns.len()) };
        assert!(handle.is_null());
    }

    #[test]
    fn c_abi_resolves_localhost_without_external_network() {
        let host = std::ffi::CString::new("localhost").unwrap();
        let mut output = [0_i8; 1024];
        // SAFETY: both buffers are valid and output is large enough.
        let result = unsafe { bp_system_resolve(host.as_ptr(), output.as_mut_ptr(), output.len()) };
        assert!(result > 0);
        // SAFETY: the C ABI function NUL-terminates a valid UTF-8 response.
        let response = unsafe { CStr::from_ptr(output.as_ptr()) }.to_str().unwrap();
        assert!(!response.is_empty());
    }
}
