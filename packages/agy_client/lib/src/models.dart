/// Strongly-typed protocol models matching `bridge/agy_bridge/protocol.py`.
enum ChannelKind {
  lan,
  tailscale,
  cloudflare;

  static ChannelKind fromWire(String raw) {
    switch (raw.toLowerCase()) {
      case 'tailscale':
        return ChannelKind.tailscale;
      case 'cloudflare':
        return ChannelKind.cloudflare;
      default:
        return ChannelKind.lan;
    }
  }

  String get wire => name;

  String get shortLabel {
    switch (this) {
      case ChannelKind.lan:
        return 'LAN';
      case ChannelKind.tailscale:
        return 'TS';
      case ChannelKind.cloudflare:
        return 'CF';
    }
  }
}

class ChannelEndpoint {
  const ChannelEndpoint({
    required this.kind,
    required this.label,
    required this.baseUrl,
    this.available = true,
    this.detail,
  });

  final ChannelKind kind;
  final String label;
  final String baseUrl;
  final bool available;
  final String? detail;

  factory ChannelEndpoint.fromJson(Map<String, dynamic> json) {
    return ChannelEndpoint(
      kind: ChannelKind.fromWire((json['kind'] ?? json['k'] ?? 'lan').toString()),
      label: (json['label'] ?? json['l'] ?? 'Endpoint').toString(),
      baseUrl: (json['baseUrl'] ?? json['u'] ?? '').toString().replaceAll(RegExp(r'/+$'), ''),
      available: json['available'] is bool ? json['available'] as bool : true,
      detail: json['detail']?.toString(),
    );
  }

  Map<String, dynamic> toJson() => {
        'kind': kind.wire,
        'label': label,
        'baseUrl': baseUrl,
        'available': available,
        if (detail != null) 'detail': detail,
      };
}

class HostProfile {
  const HostProfile({
    required this.id,
    required this.name,
    required this.deviceId,
    required this.endpoints,
  });

  final String id;
  final String name;
  final String deviceId;
  final List<ChannelEndpoint> endpoints;

  HostProfile copyWith({
    String? name,
    String? deviceId,
    List<ChannelEndpoint>? endpoints,
  }) {
    return HostProfile(
      id: id,
      name: name ?? this.name,
      deviceId: deviceId ?? this.deviceId,
      endpoints: endpoints ?? this.endpoints,
    );
  }

  factory HostProfile.fromJson(Map<String, dynamic> json) {
    final rawEps = (json['endpoints'] as List<dynamic>? ?? const []);
    return HostProfile(
      id: json['id'].toString(),
      name: json['name'].toString(),
      deviceId: (json['deviceId'] ?? '').toString(),
      endpoints: rawEps
          .whereType<Map<String, dynamic>>()
          .map(ChannelEndpoint.fromJson)
          .toList(),
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'deviceId': deviceId,
        'endpoints': endpoints.map((e) => e.toJson()).toList(),
      };
}

class PairingPayload {
  const PairingPayload({
    required this.version,
    required this.hostName,
    required this.pairingToken,
    required this.endpoints,
  });

  final int version;
  final String hostName;
  final String pairingToken;
  final List<ChannelEndpoint> endpoints;
}

class PairResult {
  const PairResult({
    required this.deviceId,
    required this.deviceToken,
    required this.hostName,
    required this.version,
  });

  final String deviceId;
  final String deviceToken;
  final String hostName;
  final String version;

  factory PairResult.fromJson(Map<String, dynamic> json) {
    return PairResult(
      deviceId: json['deviceId'].toString(),
      deviceToken: json['deviceToken'].toString(),
      hostName: json['hostName'].toString(),
      version: (json['version'] ?? '').toString(),
    );
  }
}

class ProjectInfo {
  const ProjectInfo({
    required this.id,
    required this.name,
    required this.path,
    required this.createdAtMs,
    this.sessionCount = 0,
  });

  final String id;
  final String name;
  final String path;
  final int createdAtMs;
  final int sessionCount;

  factory ProjectInfo.fromJson(Map<String, dynamic> json) {
    return ProjectInfo(
      id: json['id'].toString(),
      name: json['name'].toString(),
      path: json['path'].toString(),
      createdAtMs: (json['createdAtMs'] as num?)?.toInt() ?? 0,
      sessionCount: (json['sessionCount'] as num?)?.toInt() ?? 0,
    );
  }
}

class SessionInfo {
  const SessionInfo({
    required this.id,
    required this.projectId,
    required this.projectName,
    required this.title,
    this.model,
    this.status = 'idle',
    this.lastSeq = 0,
    required this.createdAtMs,
    required this.updatedAtMs,
  });

  final String id;
  final String projectId;
  final String projectName;
  final String title;
  final String? model;
  final String status;
  final int lastSeq;
  final int createdAtMs;
  final int updatedAtMs;

  SessionInfo copyWith({
    String? title,
    String? model,
    String? status,
    int? lastSeq,
    int? updatedAtMs,
  }) {
    return SessionInfo(
      id: id,
      projectId: projectId,
      projectName: projectName,
      title: title ?? this.title,
      model: model ?? this.model,
      status: status ?? this.status,
      lastSeq: lastSeq ?? this.lastSeq,
      createdAtMs: createdAtMs,
      updatedAtMs: updatedAtMs ?? this.updatedAtMs,
    );
  }

  factory SessionInfo.fromJson(Map<String, dynamic> json) {
    return SessionInfo(
      id: json['id'].toString(),
      projectId: json['projectId'].toString(),
      projectName: (json['projectName'] ?? '').toString(),
      title: (json['title'] ?? 'New Conversation').toString(),
      model: json['model']?.toString(),
      status: (json['status'] ?? 'idle').toString(),
      lastSeq: (json['lastSeq'] as num?)?.toInt() ?? 0,
      createdAtMs: (json['createdAtMs'] as num?)?.toInt() ?? 0,
      updatedAtMs: (json['updatedAtMs'] as num?)?.toInt() ?? 0,
    );
  }
}

class AttachmentInfo {
  const AttachmentInfo({
    required this.id,
    required this.sessionId,
    required this.filename,
    required this.mimeType,
    required this.sizeBytes,
    required this.sha256,
    required this.createdAtMs,
  });

  final String id;
  final String sessionId;
  final String filename;
  final String mimeType;
  final int sizeBytes;
  final String sha256;
  final int createdAtMs;

  factory AttachmentInfo.fromJson(Map<String, dynamic> json) {
    return AttachmentInfo(
      id: json['id'].toString(),
      sessionId: (json['sessionId'] ?? '').toString(),
      filename: (json['filename'] ?? 'image.png').toString(),
      mimeType: (json['mimeType'] ?? 'image/png').toString(),
      sizeBytes: (json['sizeBytes'] as num?)?.toInt() ?? 0,
      sha256: (json['sha256'] ?? '').toString(),
      createdAtMs: (json['createdAtMs'] as num?)?.toInt() ?? 0,
    );
  }
}

class ToolEventRecord {
  const ToolEventRecord({
    required this.id,
    required this.name,
    this.args = const {},
    this.status = 'running',
    this.outputPreview,
  });

  final String id;
  final String name;
  final Map<String, dynamic> args;
  final String status;
  final String? outputPreview;

  factory ToolEventRecord.fromJson(Map<String, dynamic> json) {
    return ToolEventRecord(
      id: (json['id'] ?? '').toString(),
      name: (json['name'] ?? 'tool').toString(),
      args: (json['args'] as Map<String, dynamic>?) ?? const {},
      status: (json['status'] ?? 'running').toString(),
      outputPreview: json['outputPreview']?.toString(),
    );
  }
}

class MessageRecord {
  const MessageRecord({
    required this.id,
    required this.sessionId,
    required this.turnId,
    required this.role,
    this.content = '',
    this.thoughts = '',
    this.toolEvents = const [],
    this.attachments = const [],
    this.status = 'completed',
    this.errorMessage,
    required this.seq,
    required this.createdAtMs,
    required this.updatedAtMs,
  });

  final String id;
  final String sessionId;
  final String turnId;
  final String role;
  final String content;
  final String thoughts;
  final List<ToolEventRecord> toolEvents;
  final List<AttachmentInfo> attachments;
  final String status;
  final String? errorMessage;
  final int seq;
  final int createdAtMs;
  final int updatedAtMs;

  MessageRecord copyWith({
    String? content,
    String? thoughts,
    List<ToolEventRecord>? toolEvents,
    List<AttachmentInfo>? attachments,
    String? status,
    String? errorMessage,
    int? seq,
    int? updatedAtMs,
  }) {
    return MessageRecord(
      id: id,
      sessionId: sessionId,
      turnId: turnId,
      role: role,
      content: content ?? this.content,
      thoughts: thoughts ?? this.thoughts,
      toolEvents: toolEvents ?? this.toolEvents,
      attachments: attachments ?? this.attachments,
      status: status ?? this.status,
      errorMessage: errorMessage ?? this.errorMessage,
      seq: seq ?? this.seq,
      createdAtMs: createdAtMs,
      updatedAtMs: updatedAtMs ?? this.updatedAtMs,
    );
  }

  factory MessageRecord.fromJson(Map<String, dynamic> json) {
    return MessageRecord(
      id: json['id'].toString(),
      sessionId: json['sessionId'].toString(),
      turnId: (json['turnId'] ?? '').toString(),
      role: (json['role'] ?? 'assistant').toString(),
      content: (json['content'] ?? '').toString(),
      thoughts: (json['thoughts'] ?? '').toString(),
      toolEvents: (json['toolEvents'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(ToolEventRecord.fromJson)
          .toList(),
      attachments: (json['attachments'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(AttachmentInfo.fromJson)
          .toList(),
      status: (json['status'] ?? 'completed').toString(),
      errorMessage: json['errorMessage']?.toString(),
      seq: (json['seq'] as num?)?.toInt() ?? 0,
      createdAtMs: (json['createdAtMs'] as num?)?.toInt() ?? 0,
      updatedAtMs: (json['updatedAtMs'] as num?)?.toInt() ?? 0,
    );
  }
}

class MessagesPage {
  const MessagesPage({
    required this.sessionId,
    required this.messages,
    required this.hasMore,
    required this.lastSeq,
  });

  final String sessionId;
  final List<MessageRecord> messages;
  final bool hasMore;
  final int lastSeq;

  factory MessagesPage.fromJson(Map<String, dynamic> json) {
    return MessagesPage(
      sessionId: json['sessionId'].toString(),
      messages: (json['messages'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(MessageRecord.fromJson)
          .toList(),
      hasMore: json['hasMore'] == true,
      lastSeq: (json['lastSeq'] as num?)?.toInt() ?? 0,
    );
  }
}

class ChannelsStatus {
  const ChannelsStatus({
    required this.hostName,
    required this.port,
    required this.pairingUri,
    required this.endpoints,
    required this.tailscaleInstalled,
    required this.cloudflaredInstalled,
    required this.cloudflaredMode,
    required this.cloudflaredRunning,
    required this.hasNamedTunnelToken,
  });

  final String hostName;
  final int port;
  final String pairingUri;
  final List<ChannelEndpoint> endpoints;
  final bool tailscaleInstalled;
  final bool cloudflaredInstalled;
  final String cloudflaredMode;
  final bool cloudflaredRunning;
  final bool hasNamedTunnelToken;

  factory ChannelsStatus.fromJson(Map<String, dynamic> json) {
    return ChannelsStatus(
      hostName: (json['hostName'] ?? '').toString(),
      port: (json['port'] as num?)?.toInt() ?? 18790,
      pairingUri: (json['pairingUri'] ?? '').toString(),
      endpoints: (json['endpoints'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(ChannelEndpoint.fromJson)
          .toList(),
      tailscaleInstalled: json['tailscaleInstalled'] == true,
      cloudflaredInstalled: json['cloudflaredInstalled'] == true,
      cloudflaredMode: (json['cloudflaredMode'] ?? 'off').toString(),
      cloudflaredRunning: json['cloudflaredRunning'] == true,
      hasNamedTunnelToken: json['hasNamedTunnelToken'] == true,
    );
  }
}

class StreamEvent {
  const StreamEvent({
    required this.type,
    this.sessionId,
    this.turnId,
    this.messageId,
    this.seq,
    this.data = const {},
    required this.timestampMs,
  });

  final String type;
  final String? sessionId;
  final String? turnId;
  final String? messageId;
  final int? seq;
  final Map<String, dynamic> data;
  final int timestampMs;

  factory StreamEvent.fromJson(Map<String, dynamic> json) {
    return StreamEvent(
      type: (json['type'] ?? 'error').toString(),
      sessionId: json['sessionId']?.toString(),
      turnId: json['turnId']?.toString(),
      messageId: json['messageId']?.toString(),
      seq: (json['seq'] as num?)?.toInt(),
      data: (json['data'] as Map<String, dynamic>?) ?? const {},
      timestampMs: (json['timestampMs'] as num?)?.toInt() ?? 0,
    );
  }
}
