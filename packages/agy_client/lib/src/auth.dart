import 'dart:convert';

import 'models.dart';

/// Parses a QR pairing URI produced by `agy-bridge pair`:
/// `agygo://pair?v=1&name=<host>&token=<48hex>&eps=<base64url(json)>`
PairingPayload parsePairingUri(String raw) {
  final trimmed = raw.trim();
  final uri = Uri.tryParse(trimmed);
  if (uri == null || uri.scheme.toLowerCase() != 'agygo' || uri.host.toLowerCase() != 'pair') {
    throw const FormatException('Expected an agygo://pair?... pairing URI');
  }

  final version = int.tryParse(uri.queryParameters['v'] ?? '1') ?? 1;
  final token = (uri.queryParameters['token'] ?? '').trim();
  if (token.length < 24) {
    throw const FormatException('Pairing URI is missing a valid pairing token');
  }

  final hostName = (uri.queryParameters['name'] ?? 'Desktop').trim();
  final encodedEps = (uri.queryParameters['eps'] ?? '').trim();
  if (encodedEps.isEmpty) {
    throw const FormatException('Pairing URI contains no connection endpoints');
  }

  final normalized = base64Url.normalize(encodedEps);
  final decoded = jsonDecode(utf8.decode(base64Url.decode(normalized)));
  if (decoded is! List) {
    throw const FormatException('Malformed endpoints payload in pairing URI');
  }

  final endpoints = decoded
      .whereType<Map<String, dynamic>>()
      .map(ChannelEndpoint.fromJson)
      .where((e) => e.baseUrl.startsWith('http://') || e.baseUrl.startsWith('https://'))
      .toList();

  if (endpoints.isEmpty) {
    throw const FormatException('No valid HTTP/HTTPS endpoints found in pairing URI');
  }

  return PairingPayload(
    version: version,
    hostName: hostName.isEmpty ? 'Desktop' : hostName,
    pairingToken: token,
    endpoints: endpoints,
  );
}
