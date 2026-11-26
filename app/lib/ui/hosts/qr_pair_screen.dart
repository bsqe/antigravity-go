import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../../state/hosts_state.dart';

class QrPairScreen extends ConsumerStatefulWidget {
  const QrPairScreen({super.key});

  @override
  ConsumerState<QrPairScreen> createState() => _QrPairScreenState();
}

class _QrPairScreenState extends ConsumerState<QrPairScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs;
  final _uriController = TextEditingController();
  final _urlController = TextEditingController();
  final _tokenController = TextEditingController();
  final _nameController = TextEditingController();

  bool _pairing = false;
  bool _scannedOnce = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _tabs = TabController(length: 2, vsync: this);
  }

  @override
  void dispose() {
    _tabs.dispose();
    _uriController.dispose();
    _urlController.dispose();
    _tokenController.dispose();
    _nameController.dispose();
    super.dispose();
  }

  Future<void> _submitPairingUri(String rawUri) async {
    if (_pairing) return;
    setState(() {
      _pairing = true;
      _error = null;
    });
    try {
      await ref.read(hostsProvider.notifier).pairFromUri(rawUri);
      if (mounted) Navigator.of(context).pop(true);
    } catch (err) {
      setState(() {
        _pairing = false;
        _scannedOnce = false;
        _error = '配对失败: $err';
      });
    }
  }

  Future<void> _submitManual() async {
    if (_pairing) return;
    final rawUri = _uriController.text.trim();
    if (rawUri.startsWith('agygo://')) {
      await _submitPairingUri(rawUri);
      return;
    }

    final url = _urlController.text.trim();
    final token = _tokenController.text.trim();
    if (url.isEmpty || token.isEmpty) {
      setState(() => _error = '请填写配对链接，或填写电脑地址与 48 位配对口令');
      return;
    }

    setState(() {
      _pairing = true;
      _error = null;
    });
    try {
      await ref.read(hostsProvider.notifier).pairManual(
            baseUrl: url,
            pairingToken: token,
            hostLabel: _nameController.text.trim(),
          );
      if (mounted) Navigator.of(context).pop(true);
    } catch (err) {
      setState(() {
        _pairing = false;
        _error = '手动配对失败: $err';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('配对电脑 Bridge'),
        bottom: TabBar(
          controller: _tabs,
          tabs: const [
            Tab(icon: Icon(Icons.qr_code_scanner), text: '扫码配对'),
            Tab(icon: Icon(Icons.edit_note), text: '粘贴/手动'),
          ],
        ),
      ),
      body: Column(
        children: [
          if (_error != null)
            MaterialBanner(
              content: Text(_error!),
              actions: [
                TextButton(
                  onPressed: () => setState(() => _error = null),
                  child: const Text('关闭'),
                ),
              ],
            ),
          if (_pairing) const LinearProgressIndicator(),
          Expanded(
            child: TabBarView(
              controller: _tabs,
              children: [
                _buildScannerTab(),
                _buildManualTab(),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildScannerTab() {
    return Stack(
      children: [
        MobileScanner(
          onDetect: (capture) {
            if (_scannedOnce || _pairing) return;
            for (final barcode in capture.barcodes) {
              final value = barcode.rawValue;
              if (value != null && value.startsWith('agygo://pair')) {
                _scannedOnce = true;
                _submitPairingUri(value);
                break;
              }
            }
          },
        ),
        Align(
          alignment: Alignment.bottomCenter,
          child: Container(
            width: double.infinity,
            padding: const EdgeInsets.all(16),
            color: Colors.black.withValues(alpha: 0.6),
            child: const Text(
              '在电脑终端运行 agy-bridge pair，对准显示的二维码即可自动写入 LAN / Tailscale / Cloudflare 全通道',
              style: TextStyle(color: Colors.white),
              textAlign: TextAlign.center,
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildManualTab() {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        TextField(
          controller: _uriController,
          decoration: const InputDecoration(
            labelText: '方式 A：直接粘贴 agygo://pair?... 配对链接',
            border: OutlineInputBorder(),
          ),
          minLines: 2,
          maxLines: 3,
        ),
        const Padding(
          padding: EdgeInsets.symmetric(vertical: 16),
          child: Row(
            children: [
              Expanded(child: Divider()),
              Padding(
                padding: EdgeInsets.symmetric(horizontal: 12),
                child: Text('或手动填写'),
              ),
              Expanded(child: Divider()),
            ],
          ),
        ),
        TextField(
          controller: _nameController,
          decoration: const InputDecoration(
            labelText: '主机名称（可选）',
            hintText: '如 Home Windows PC',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _urlController,
          decoration: const InputDecoration(
            labelText: 'Bridge 地址（LAN / Tailscale / Cloudflare）',
            hintText: 'http://192.168.1.10:18790 或 https://xxx.trycloudflare.com',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _tokenController,
          decoration: const InputDecoration(
            labelText: '48 位配对口令',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 20),
        FilledButton.icon(
          onPressed: _pairing ? null : _submitManual,
          icon: const Icon(Icons.link),
          label: const Text('验证并配对'),
        ),
      ],
    );
  }
}
