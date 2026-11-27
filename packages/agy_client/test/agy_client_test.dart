import 'dart:async';
import 'dart:convert';

import 'package:agy_client/agy_client.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:test/test.dart';

void main() {
  group('parsePairingUri', () {
    test('parses valid multi-endpoint agygo://pair URI', () {
      final epsJson = jsonEncode([
        {'k': 'lan', 'u': 'http://192.168.1.10:18790', 'l': 'LAN (Wi-Fi)'},
        {'k': 'cloudflare', 'u': 'https://demo.trycloudflare.com', 'l': 'Cloudflare'},
      ]);
      final encoded = base64Url.encode(utf8.encode(epsJson)).replaceAll('=', '');
      final token = List.filled(48, 'a').join();
      final uri = 'agygo://pair?v=1&name=My%20PC&token=$token&eps=$encoded';

      final parsed = parsePairingUri(uri);
      expect(parsed.version, 1);
      expect(parsed.hostName, 'My PC');
      expect(parsed.pairingToken, token);
      expect(parsed.endpoints.length, 2);
      expect(parsed.endpoints[0].kind, ChannelKind.lan);
      expect(parsed.endpoints[0].baseUrl, 'http://192.168.1.10:18790');
      expect(parsed.endpoints[1].kind, ChannelKind.cloudflare);
    });

    test('rejects invalid scheme or short token', () {
      expect(() => parsePairingUri('https://example.com'), throwsFormatException);
      expect(() => parsePairingUri('agygo://pair?v=1&token=short&eps=W10'), throwsFormatException);
    });
  });

  group('ChannelRacer', () {
    test('picks fastest reachable endpoint among LAN, Tailscale, and Cloudflare', () async {
      final client = MockClient((req) async {
        if (req.url.host == '192.168.1.10') {
          return http.Response('unreachable', 503);
        }
        if (req.url.host == '100.88.1.2') {
          await Future<void>.delayed(const Duration(milliseconds: 5));
          return http.Response(
            jsonEncode({'ok': true, 'hostName': 'WorkPC', 'version': '0.1.0'}),
            200,
          );
        }
        await Future<void>.delayed(const Duration(milliseconds: 60));
        return http.Response(
          jsonEncode({'ok': true, 'hostName': 'WorkPC', 'version': '0.1.0'}),
          200,
        );
      });

      final racer = ChannelRacer(httpClient: client);
      final best = await racer.selectFastest(const [
        ChannelEndpoint(kind: ChannelKind.lan, label: 'LAN', baseUrl: 'http://192.168.1.10:18790'),
        ChannelEndpoint(
          kind: ChannelKind.tailscale,
          label: 'Tailscale',
          baseUrl: 'http://100.88.1.2:18790',
        ),
        ChannelEndpoint(
          kind: ChannelKind.cloudflare,
          label: 'Cloudflare',
          baseUrl: 'https://demo.trycloudflare.com',
        ),
      ]);

      expect(best, isNotNull);
      expect(best!.endpoint.kind, ChannelKind.tailscale);
      expect(best.hostName, 'WorkPC');
    });
  });

  group('AgyHttpApi', () {
    test('sends Bearer authorization and parses projects/sessions', () async {
      String? seenAuth;
      final mock = MockClient((req) async {
        seenAuth = req.headers['Authorization'];
        if (req.url.path == '/api/v1/projects') {
          return http.Response(
            jsonEncode([
              {
                'id': 'proj_1',
                'name': 'Repo',
                'path': '/home/dev/repo',
                'createdAtMs': 1000,
                'sessionCount': 2,
              }
            ]),
            200,
          );
        }
        return http.Response('not found', 404);
      });

      final api = AgyHttpApi(
        baseUrl: 'http://127.0.0.1:18790',
        deviceToken: 'agygo_secret',
        httpClient: mock,
      );
      final projects = await api.listProjects();
      expect(seenAuth, 'Bearer agygo_secret');
      expect(projects.length, 1);
      expect(projects.first.name, 'Repo');
      expect(projects.first.sessionCount, 2);
    });

    test('parses desktop conversations, handoff continuation, sync, and settings', () async {
      final mock = MockClient((req) async {
        if (req.url.path == '/api/v1/desktop/conversations') {
          return http.Response(
            jsonEncode([
              {
                'id': 'desktop-1111-2222-3333-444455556666',
                'title': 'Fix build issue',
                'preview': 'All tests passed.',
                'turnCount': 3,
                'messageCount': 6,
                'stepCount': 20,
                'artifacts': [
                  {
                    'name': 'implementation_plan.md',
                    'sizeBytes': 1024,
                    'updatedAtMs': 5000,
                    'summary': 'Plan summary',
                  }
                ],
                'inferredProjectId': 'proj_1',
                'linkedSessionId': null,
                'updatedAtMs': 6000,
              }
            ]),
            200,
          );
        }
        if (req.url.path == '/api/v1/desktop/conversations/desktop-1111-2222-3333-444455556666/continue') {
          return http.Response.bytes(
            utf8.encode(
              jsonEncode({
                'session': {
                  'id': 'ses_cont_1',
                  'projectId': 'proj_1',
                  'projectName': 'Repo',
                  'title': '[接力] Fix build issue',
                  'status': 'idle',
                  'lastSeq': 6,
                  'sourceDesktopId': 'desktop-1111-2222-3333-444455556666',
                  'createdAtMs': 7000,
                  'updatedAtMs': 7000,
                },
                'importedMessages': 6,
                'reused': false,
              }),
            ),
            201,
            headers: const {'content-type': 'application/json; charset=utf-8'},
          );
        }
        if (req.url.path == '/api/v1/sessions/ses_cont_1/sync-desktop') {
          return http.Response(
            jsonEncode({
              'sessionId': 'ses_cont_1',
              'sourceDesktopId': 'desktop-1111-2222-3333-444455556666',
              'addedMessages': 2,
              'syncedStep': 24,
            }),
            200,
          );
        }
        if (req.url.path == '/api/v1/settings') {
          return http.Response(
            jsonEncode({
              'toolPolicy': 'edit_files',
              'defaultModel': '',
              'desktopBrainDir': '/home/dev/.gemini/antigravity/brain',
              'desktopSyncAvailable': true,
            }),
            200,
          );
        }
        return http.Response('not found', 404);
      });

      final api = AgyHttpApi(
        baseUrl: 'http://127.0.0.1:18790',
        deviceToken: 'agygo_secret',
        httpClient: mock,
      );

      final desktopList = await api.listDesktopConversations();
      expect(desktopList.length, 1);
      expect(desktopList.first.title, 'Fix build issue');
      expect(desktopList.first.artifacts.first.name, 'implementation_plan.md');

      final cont = await api.continueDesktopConversation(
        'desktop-1111-2222-3333-444455556666',
        projectId: 'proj_1',
      );
      expect(cont.importedMessages, 6);
      expect(cont.session.sourceDesktopId, 'desktop-1111-2222-3333-444455556666');

      final sync = await api.syncSessionFromDesktop('ses_cont_1');
      expect(sync.addedMessages, 2);
      expect(sync.syncedStep, 24);

      final settings = await api.updateSettings(toolPolicy: 'edit_files');
      expect(settings.toolPolicy, 'edit_files');
      expect(settings.desktopSyncAvailable, isTrue);
    });
  });
}
