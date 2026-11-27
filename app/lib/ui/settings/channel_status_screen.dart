import 'package:agy_client/agy_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../state/connection_state.dart';
import '../../state/hosts_state.dart';
import '../hosts/qr_pair_screen.dart';

class ChannelStatusScreen extends ConsumerStatefulWidget {
  const ChannelStatusScreen({super.key});

  @override
  ConsumerState<ChannelStatusScreen> createState() => _ChannelStatusScreenState();
}

class _ChannelStatusScreenState extends ConsumerState<ChannelStatusScreen> {
  ChannelsStatus? _remoteStatus;
  BridgeSettings? _settings;
  bool _togglingTunnel = false;
  bool _updatingPolicy = false;
  String? _tunnelError;
  String? _policyError;

  @override
  void initState() {
    super.initState();
    Future.microtask(_loadRemoteState);
  }

  Future<void> _loadRemoteState() async {
    final api = ref.read(activeConnectionProvider).httpApi;
    if (api == null) return;
    try {
      final status = await api.getChannels();
      if (mounted) setState(() => _remoteStatus = status);
    } catch (_) {}
    try {
      final settings = await api.getSettings();
      if (mounted) setState(() => _settings = settings);
    } catch (_) {}
  }

  Future<void> _updateToolPolicy(String policy) async {
    final api = ref.read(activeConnectionProvider).httpApi;
    if (api == null || _updatingPolicy) return;
    setState(() {
      _updatingPolicy = true;
      _policyError = null;
    });
    try {
      final updated = await api.updateSettings(toolPolicy: policy);
      if (mounted) {
        setState(() {
          _settings = updated;
          _updatingPolicy = false;
        });
      }
    } catch (err) {
      if (mounted) {
        setState(() {
          _updatingPolicy = false;
          _policyError = err.toString();
        });
      }
    }
  }

  Future<void> _toggleQuickTunnel(bool enable) async {
    final api = ref.read(activeConnectionProvider).httpApi;
    final host = ref.read(activeConnectionProvider).host;
    if (api == null || host == null) return;
    setState(() {
      _togglingTunnel = true;
      _tunnelError = null;
    });
    try {
      final next = await api.configureTunnel(mode: enable ? 'quick' : 'off');
      await ref.read(hostsProvider.notifier).updateHostEndpoints(host.id, next.endpoints);
      if (mounted) {
        setState(() {
          _remoteStatus = next;
          _togglingTunnel = false;
        });
      }
    } catch (err) {
      if (mounted) {
        setState(() {
          _togglingTunnel = false;
          _tunnelError = err.toString();
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final hostsState = ref.watch(hostsProvider).valueOrNull ?? const HostsState();
    final conn = ref.watch(activeConnectionProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('主机与跨网通道'),
        actions: [
          IconButton(
            tooltip: '重新测速',
            onPressed: () async {
              await ref.read(activeConnectionProvider.notifier).reconnect();
              await _loadRemoteState();
            },
            icon: const Icon(Icons.speed),
          ),
          IconButton(
            tooltip: '扫码新增主机',
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute<void>(builder: (_) => const QrPairScreen()),
              );
            },
            icon: const Icon(Icons.qr_code_scanner),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('已配对主机', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          if (hostsState.hosts.isEmpty)
            const Card(
              child: Padding(
                padding: EdgeInsets.all(16),
                child: Text('暂未配对任何电脑主机，点击右上角扫码配对。'),
              ),
            )
          else
            ...hostsState.hosts.map((h) {
              final selected = h.id == hostsState.activeHost?.id;
              return Card(
                child: ListTile(
                  leading: Icon(
                    selected ? Icons.radio_button_checked : Icons.computer,
                    color: selected ? Theme.of(context).colorScheme.primary : null,
                  ),
                  title: Text(h.name),
                  subtitle: Text('${h.endpoints.length} 条通道'),
                  trailing: IconButton(
                    icon: const Icon(Icons.delete_outline),
                    onPressed: () => ref.read(hostsProvider.notifier).removeHost(h.id),
                  ),
                  onTap: () => ref.read(hostsProvider.notifier).selectHost(h.id),
                ),
              );
            }),
          const SizedBox(height: 20),
          Text('当前通道测速结果', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          if (conn.probes.isEmpty)
            const Card(
              child: Padding(
                padding: EdgeInsets.all(16),
                child: Text('尚无测速记录'),
              ),
            )
          else
            ...conn.probes.map((p) {
              final active = conn.activeEndpoint?.baseUrl == p.endpoint.baseUrl;
              return Card(
                child: ListTile(
                  leading: Chip(label: Text(p.endpoint.kind.shortLabel)),
                  title: Text(p.endpoint.label),
                  subtitle: Text(p.endpoint.baseUrl),
                  trailing: p.reachable
                      ? Text(
                          '${p.latencyMs ?? 0} ms${active ? " · 当前" : ""}',
                          style: TextStyle(
                            color: active
                                ? Theme.of(context).colorScheme.primary
                                : Colors.green,
                            fontWeight: FontWeight.w600,
                          ),
                        )
                      : Text(
                          '不可达',
                          style: TextStyle(color: Theme.of(context).colorScheme.error),
                        ),
                ),
              );
            }),
          if (_remoteStatus != null) ...[
            const SizedBox(height: 20),
            Text('电脑端跨网穿透状态', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            Card(
              child: Column(
                children: [
                  ListTile(
                    leading: const Icon(Icons.vpn_lock_outlined),
                    title: const Text('Tailscale 私网'),
                    subtitle: Text(
                      _remoteStatus!.tailscaleInstalled
                          ? '已安装，自动探测 100.64.x.x / MagicDNS'
                          : '未检测到 Tailscale 客户端',
                    ),
                  ),
                  const Divider(height: 1),
                  SwitchListTile(
                    secondary: const Icon(Icons.cloud_outlined),
                    title: const Text('Cloudflare 免登录临时隧道'),
                    subtitle: Text(
                      _tunnelError ??
                          (_remoteStatus!.cloudflaredInstalled
                              ? (_remoteStatus!.cloudflaredRunning
                                  ? '运行中 (${_remoteStatus!.cloudflaredMode})'
                                  : '已安装 cloudflared，当前未开启')
                              : '电脑未安装 cloudflared'),
                    ),
                    value: _remoteStatus!.cloudflaredRunning &&
                        _remoteStatus!.cloudflaredMode == 'quick',
                    onChanged: _togglingTunnel || !_remoteStatus!.cloudflaredInstalled
                        ? null
                        : _toggleQuickTunnel,
                  ),
                ],
              ),
            ),
          ],
          if (_settings != null) ...[
            const SizedBox(height: 20),
            Text('Agent 远程操作权限', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            Card(
              child: Column(
                children: [
                  if (_policyError != null)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                      child: Text(
                        _policyError!,
                        style: TextStyle(color: Theme.of(context).colorScheme.error),
                      ),
                    ),
                  _buildPolicyTile(
                    context,
                    value: 'read_only',
                    title: '只读安全模式（默认）',
                    subtitle: '仅查看文件、搜索代码与检索网页，禁止修改文件与运行命令',
                    selected: _settings!.toolPolicy == 'read_only',
                  ),
                  const Divider(height: 1),
                  _buildPolicyTile(
                    context,
                    value: 'edit_files',
                    title: '允许修改工作区文件',
                    subtitle: '可在已登记项目目录内创建与编辑代码文件，禁止运行终端命令',
                    selected: _settings!.toolPolicy == 'edit_files',
                  ),
                  const Divider(height: 1),
                  _buildPolicyTile(
                    context,
                    value: 'full',
                    title: '完整执行（改文件 + 跑命令）',
                    subtitle: '允许修改工作区文件并运行终端构建/测试命令，请谨慎开启',
                    selected: _settings!.toolPolicy == 'full',
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildPolicyTile(
    BuildContext context, {
    required String value,
    required String title,
    required String subtitle,
    required bool selected,
  }) {
    return ListTile(
      enabled: !_updatingPolicy,
      leading: Icon(
        selected ? Icons.radio_button_checked : Icons.radio_button_unchecked,
        color: selected ? Theme.of(context).colorScheme.primary : null,
      ),
      title: Text(title),
      subtitle: Text(subtitle),
      onTap: selected ? null : () => _updateToolPolicy(value),
    );
  }
}
