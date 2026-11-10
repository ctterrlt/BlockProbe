// SPDX-License-Identifier: AGPL-3.0-or-later
/// `BlockProbeAndroidNetwork` Flet service: reads the active Android network.
library;

import 'dart:async';

import 'package:flet/flet.dart';
import 'package:flutter/services.dart';

/// Bridges the Python `AndroidNetworkService` control to the Kotlin plugin.
///
/// The Kotlin side answers `read_state`; every failure is returned as a map so
/// the Python adapter can degrade to "unavailable" instead of raising.
class AndroidNetworkService extends FletService {
  AndroidNetworkService({required super.control});

  static const MethodChannel _channel = MethodChannel(
    'blockprobe_android_network',
  );

  @override
  void init() {
    super.init();
    control.addInvokeMethodListener(invokeMethod);
  }

  @override
  void dispose() {
    control.removeInvokeMethodListener(invokeMethod);
    super.dispose();
  }

  /// Dispatch a Python `_invoke_method` call.
  Future<dynamic> invokeMethod(String name, [dynamic args]) async {
    switch (name) {
      case 'read_state':
        try {
          final result = await _channel.invokeMapMethod<String, dynamic>(
            'read_state',
          );
          if (result == null) {
            return <String, dynamic>{
              'available': false,
              'error': 'empty_response',
            };
          }
          return result;
        } on PlatformException catch (error) {
          return <String, dynamic>{
            'available': false,
            'error': error.code.isEmpty ? 'platform_error' : error.code,
          };
        } on MissingPluginException {
          return <String, dynamic>{
            'available': false,
            'error': 'unsupported_platform',
          };
        }
      case 'open_network_settings':
        try {
          return await _channel.invokeMethod<bool>('open_network_settings') ??
              false;
        } on PlatformException {
          return false;
        } on MissingPluginException {
          return false;
        }
      default:
        return 'error:unknown_method:$name';
    }
  }
}
