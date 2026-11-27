import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'models.dart';

class ChannelProbeResult {
  const ChannelProbeResult({
    required this.endpoint,
    required this.reachable,
    this.latencyMs,
    this.hostName,
    this.version,
    this.error,
  });

  final ChannelEndpoint endpoint;
  final bool reachable;
  final int? latencyMs;
  final String? hostName;
  final String? version;
  final String? error;
}

/// Probes all configured channels (LAN, Tailscale, Cloudflare) concurrently and
/// selects the lowest-latency reachable endpoint.
class ChannelRacer {
  ChannelRacer({
    http.Client? httpClient,
    this.timeout = const Duration(seconds: 8),
  }) : _http = httpClient ?? http.Client();

  final http.Client _http;
  final Duration timeout;

  Future<ChannelProbeResult> probeOne(ChannelEndpoint endpoint) async {
    final sw = Stopwatch()..start();
    try {
      final uri = Uri.parse('${endpoint.baseUrl}/api/v1/ping');
      final res = await _http.get(uri).timeout(timeout);
      sw.stop();
      if (res.statusCode != 200) {
        return ChannelProbeResult(
          endpoint: endpoint,
          reachable: false,
          error: 'HTTP ${res.statusCode}',
        );
      }
      final body = jsonDecode(res.body);
      if (body is! Map<String, dynamic> || body['ok'] != true) {
        return ChannelProbeResult(
          endpoint: endpoint,
          reachable: false,
          error: 'Unexpected ping response',
        );
      }
      return ChannelProbeResult(
        endpoint: endpoint,
        reachable: true,
        latencyMs: sw.elapsedMilliseconds,
        hostName: body['hostName']?.toString(),
        version: body['version']?.toString(),
      );
    } catch (err) {
      sw.stop();
      return ChannelProbeResult(
        endpoint: endpoint,
        reachable: false,
        error: err.toString(),
      );
    }
  }

  /// Probes all endpoints concurrently and returns every probe result sorted
  /// with reachable + lowest latency first.
  Future<List<ChannelProbeResult>> probeAll(List<ChannelEndpoint> endpoints) async {
    final active = endpoints.where((e) => e.available && e.baseUrl.isNotEmpty).toList();
    if (active.isEmpty) return const [];
    final results = await Future.wait(active.map(probeOne));
    results.sort((a, b) {
      if (a.reachable != b.reachable) return a.reachable ? -1 : 1;
      if (!a.reachable) return 0;
      return (a.latencyMs ?? 999999).compareTo(b.latencyMs ?? 999999);
    });
    return results;
  }

  /// Picks the first reachable endpoint as soon as it answers (returning in ~10ms
  /// on LAN while still allowing up to `timeout` for Cloudflare DNS/TLS handshakes).
  Future<ChannelProbeResult?> selectFastest(List<ChannelEndpoint> endpoints) async {
    final active = endpoints.where((e) => e.available && e.baseUrl.isNotEmpty).toList();
    if (active.isEmpty) return null;

    final completer = Completer<ChannelProbeResult?>();
    var remaining = active.length;

    for (final ep in active) {
      unawaited(
        probeOne(ep).then((result) {
          if (result.reachable && !completer.isCompleted) {
            completer.complete(result);
            return;
          }
          remaining -= 1;
          if (remaining == 0 && !completer.isCompleted) {
            completer.complete(null);
          }
        }),
      );
    }
    return completer.future;
  }
}
