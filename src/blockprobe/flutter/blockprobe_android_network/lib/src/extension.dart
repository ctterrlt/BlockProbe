// SPDX-License-Identifier: AGPL-3.0-or-later
/// Registers the BlockProbe Android network service with Flet.
library;

import 'package:flet/flet.dart';
import 'package:flutter/widgets.dart';

import 'android_network_service.dart';

/// Flet user extension entry point.
///
/// `flet build` imports `package:blockprobe_android_network/blockprobe_android_network.dart`
/// and instantiates `Extension()` for every bundled Flutter extension.
class Extension extends FletExtension {
  @override
  void ensureInitialized() {
    // The service has no global state; each control owns its channel call.
  }

  @override
  FletService? createService(Control control) {
    switch (control.type) {
      case 'BlockProbeAndroidNetwork':
        return AndroidNetworkService(control: control);
      default:
        return null;
    }
  }

  @override
  Widget? createWidget(Key? key, Control control) {
    return null;
  }
}
