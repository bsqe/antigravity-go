import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'models.dart';

class AgyApiException implements Exception {
  const AgyApiException(this.statusCode, this.message);

  final int statusCode;
  final String message;

  @override
  String toString() => 'AgyApiException($statusCode): $message';
}

/// Typed REST client for Antigravity-Go Bridge `/api/v1/*`.
class AgyHttpApi {
  AgyHttpApi({
    required String baseUrl,
    this.deviceToken,
    http.Client? httpClient,
  })  : baseUrl = baseUrl.replaceAll(RegExp(r'/+$'), ''),
        _http = httpClient ?? http.Client();

  final String baseUrl;
  final String? deviceToken;
  final http.Client _http;

  Map<String, String> get authHeaders => {
        if (deviceToken != null && deviceToken!.isNotEmpty)
          'Authorization': 'Bearer $deviceToken',
      };

  Map<String, String> get _jsonHeaders => {
        'Content-Type': 'application/json',
        ...authHeaders,
      };

  Uri _uri(String path, [Map<String, String>? query]) {
    final base = Uri.parse('$baseUrl$path');
    return query == null || query.isEmpty ? base : base.replace(queryParameters: query);
  }

  dynamic _decode(http.Response res) {
    if (res.statusCode < 200 || res.statusCode >= 300) {
      var detail = res.body;
      try {
        final parsed = jsonDecode(res.body);
        if (parsed is Map<String, dynamic> && parsed['detail'] != null) {
          detail = parsed['detail'].toString();
        }
      } catch (_) {}
      throw AgyApiException(res.statusCode, detail);
    }
    if (res.body.isEmpty) return null;
    return jsonDecode(utf8.decode(res.bodyBytes));
  }

  Future<PairResult> pair({
    required String pairingToken,
    required String deviceName,
    String platform = 'flutter',
  }) async {
    final res = await _http.post(
      _uri('/api/v1/auth/pair'),
      headers: const {'Content-Type': 'application/json'},
      body: jsonEncode({
        'pairingToken': pairingToken,
        'deviceName': deviceName,
        'platform': platform,
      }),
    );
    return PairResult.fromJson(_decode(res) as Map<String, dynamic>);
  }

  Future<List<ProjectInfo>> listProjects() async {
    final res = await _http.get(_uri('/api/v1/projects'), headers: authHeaders);
    final list = _decode(res) as List<dynamic>;
    return list.whereType<Map<String, dynamic>>().map(ProjectInfo.fromJson).toList();
  }

  Future<ProjectInfo> createProject({required String name, required String path}) async {
    final res = await _http.post(
      _uri('/api/v1/projects'),
      headers: _jsonHeaders,
      body: jsonEncode({'name': name, 'path': path}),
    );
    return ProjectInfo.fromJson(_decode(res) as Map<String, dynamic>);
  }

  Future<void> deleteProject(String projectId) async {
    final res = await _http.delete(_uri('/api/v1/projects/$projectId'), headers: authHeaders);
    _decode(res);
  }

  Future<List<SessionInfo>> listSessions({String? projectId}) async {
    final res = await _http.get(
      _uri('/api/v1/sessions', projectId != null ? {'projectId': projectId} : null),
      headers: authHeaders,
    );
    final list = _decode(res) as List<dynamic>;
    return list.whereType<Map<String, dynamic>>().map(SessionInfo.fromJson).toList();
  }

  Future<SessionInfo> createSession({
    required String projectId,
    String title = 'New Conversation',
    String? model,
  }) async {
    final res = await _http.post(
      _uri('/api/v1/sessions'),
      headers: _jsonHeaders,
      body: jsonEncode({
        'projectId': projectId,
        'title': title,
        if (model != null && model.isNotEmpty) 'model': model,
      }),
    );
    return SessionInfo.fromJson(_decode(res) as Map<String, dynamic>);
  }

  Future<SessionInfo> updateSession(
    String sessionId, {
    String? title,
    String? model,
  }) async {
    final res = await _http.patch(
      _uri('/api/v1/sessions/$sessionId'),
      headers: _jsonHeaders,
      body: jsonEncode({
        if (title != null) 'title': title,
        if (model != null) 'model': model,
      }),
    );
    return SessionInfo.fromJson(_decode(res) as Map<String, dynamic>);
  }

  Future<void> deleteSession(String sessionId) async {
    final res = await _http.delete(_uri('/api/v1/sessions/$sessionId'), headers: authHeaders);
    _decode(res);
  }

  Future<MessagesPage> getMessages(
    String sessionId, {
    int? beforeSeq,
    int limit = 50,
  }) async {
    final query = <String, String>{'limit': '$limit'};
    if (beforeSeq != null) query['beforeSeq'] = '$beforeSeq';
    final res = await _http.get(
      _uri('/api/v1/sessions/$sessionId/messages', query),
      headers: authHeaders,
    );
    return MessagesPage.fromJson(_decode(res) as Map<String, dynamic>);
  }

  Future<AttachmentInfo> uploadImage({
    required String sessionId,
    required Uint8List bytes,
    required String filename,
  }) async {
    final req = http.MultipartRequest('POST', _uri('/api/v1/sessions/$sessionId/attachments'))
      ..headers.addAll(authHeaders)
      ..files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename));
    final streamed = await _http.send(req);
    final res = await http.Response.fromStream(streamed);
    return AttachmentInfo.fromJson(_decode(res) as Map<String, dynamic>);
  }

  String attachmentDownloadUrl(String attachmentId) => '$baseUrl/api/v1/attachments/$attachmentId';

  Future<ChannelsStatus> getChannels() async {
    final res = await _http.get(_uri('/api/v1/channels'), headers: authHeaders);
    return ChannelsStatus.fromJson(_decode(res) as Map<String, dynamic>);
  }

  Future<ChannelsStatus> configureTunnel({
    required String mode,
    String? token,
    String? hostname,
  }) async {
    final res = await _http.post(
      _uri('/api/v1/channels/tunnel'),
      headers: _jsonHeaders,
      body: jsonEncode({
        'mode': mode,
        if (token != null) 'token': token,
        if (hostname != null) 'hostname': hostname,
      }),
    );
    return ChannelsStatus.fromJson(_decode(res) as Map<String, dynamic>);
  }

  void close() => _http.close();
}
