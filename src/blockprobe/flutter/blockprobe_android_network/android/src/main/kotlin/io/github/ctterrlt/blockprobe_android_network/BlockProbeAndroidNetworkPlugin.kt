// SPDX-License-Identifier: AGPL-3.0-or-later
package io.github.ctterrlt.blockprobe_android_network

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.NetworkCapabilities
import android.os.Build
import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/**
 * Read-only view of the active Android network for the BlockProbe profile.
 *
 * The plugin never changes settings and performs no network I/O: it answers
 * with the DNS servers and Private DNS state the system already resolved for
 * the network the device is currently using. `ACCESS_NETWORK_STATE` is an
 * install-time permission, so there is no runtime prompt for it.
 */
class BlockProbeAndroidNetworkPlugin : FlutterPlugin, MethodChannel.MethodCallHandler {
    private var channel: MethodChannel? = null
    private var context: Context? = null

    override fun onAttachedToEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        context = binding.applicationContext
        channel = MethodChannel(binding.binaryMessenger, CHANNEL).also {
            it.setMethodCallHandler(this)
        }
    }

    override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        channel?.setMethodCallHandler(null)
        channel = null
        context = null
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "read_state" -> result.success(readState())
            else -> result.notImplemented()
        }
    }

    private fun readState(): Map<String, Any?> {
        val state = mutableMapOf<String, Any?>(
            "available" to false,
            "dns_servers" to emptyList<String>(),
            "private_dns_active" to false,
            "private_dns_hostname" to null,
            "transports" to emptyList<String>(),
            "error" to null,
        )
        return try {
            val applicationContext = context
                ?: return state + mapOf("error" to "no_context")
            val manager = applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE)
                as? ConnectivityManager
                ?: return state + mapOf("error" to "connectivity_unavailable")
            val network = manager.activeNetwork
                ?: return state + mapOf("error" to "no_active_network")
            val link: LinkProperties? = manager.getLinkProperties(network)
            val capabilities: NetworkCapabilities? = manager.getNetworkCapabilities(network)

            val servers = link?.dnsServers
                ?.mapNotNull { it.hostAddress }
                ?.filter { it.isNotEmpty() }
                ?.distinct()
                ?: emptyList()

            val transports = mutableListOf<String>()
            if (capabilities != null) {
                if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) {
                    transports.add("wifi")
                }
                if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) {
                    transports.add("cellular")
                }
                if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) {
                    transports.add("ethernet")
                }
                if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) {
                    transports.add("vpn")
                }
            }

            val privateDnsSupported = Build.VERSION.SDK_INT >= Build.VERSION_CODES.P
            val privateDnsActive = privateDnsSupported && link?.isPrivateDnsActive == true

            state["available"] = true
            state["dns_servers"] = servers
            state["private_dns_active"] = privateDnsActive
            state["private_dns_hostname"] =
                if (privateDnsSupported && privateDnsActive) link?.privateDnsServerName else null
            state["transports"] = transports
            state
        } catch (error: SecurityException) {
            state + mapOf("error" to "permission_denied")
        } catch (error: Exception) {
            state + mapOf("error" to (error.message ?: "read_failed"))
        }
    }

    private companion object {
        const val CHANNEL = "blockprobe_android_network"
    }
}
