import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';
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
  late final MobileScannerController _scanner;
  final _picker = ImagePicker();
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
    _scanner = MobileScannerController(
      formats: const [BarcodeFormat.qrCode],
      detectionSpeed: DetectionSpeed.noDuplicates,
    );
    _tabs.addListener(_onTabChanged);
  }

  void _onTabChanged() {
    if (_tabs.indexIsChanging) return;
    if (_tabs.index == 0) {
      unawaited(_scanner.start());
    } else {
      unawaited(_scanner.stop());
    }
  }

  @override
  void dispose() {
    _tabs.removeListener(_onTabChanged);
    _tabs.dispose();
    unawaited(_scanner.dispose());
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
      if (mounted) {
        setState(() {
          _pairing = false;
          _scannedOnce = false;
          _error = '配对失败: $err';
        });
      }
    }
  }

  Future<void> _scanFromGallery() async {
    if (_pairing) return;
    final picked = await _picker.pickImage(source: ImageSource.gallery);
    if (picked == null) return;
    try {
      final capture = await _scanner.analyzeImage(picked.path);
      if (capture != null) {
        for (final barcode in capture.barcodes) {
          final value = barcode.rawValue;
          if (value != null && value.startsWith('agygo://pair')) {
            await _submitPairingUri(value);
            return;
          }
        }
      }
      if (mounted) {
        setState(() => _error = '未在所选图片中识别到有效的 Antigravity-Go 配对二维码');
      }
    } catch (err) {
      if (mounted) {
        setState(() => _error = '相册识图失败: $err');
      }
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
      if (mounted) {
        setState(() {
          _pairing = false;
          _error = '手动配对失败: $err';
        });
      }
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
          controller: _scanner,
          errorBuilder: (context, error) {
            final details = error.errorDetails?.message;
            final hint = details != null && details.isNotEmpty
                ? '${error.errorCode.name}: $details'
                : error.errorCode.name;
            return ColoredBox(
              color: Colors.black,
              child: Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(
                        Icons.videocam_off_outlined,
                        size: 48,
                        color: Colors.white70,
                      ),
                      const SizedBox(height: 12),
                      const Text(
                        '相机启动失败',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: 16,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        hint,
                        style: const TextStyle(color: Colors.white70, fontSize: 12),
                        textAlign: TextAlign.center,
                      ),
                      const SizedBox(height: 16),
                      Wrap(
                        spacing: 10,
                        runSpacing: 8,
                        alignment: WrapAlignment.center,
                        children: [
                          FilledButton.icon(
                            onPressed: () => unawaited(_scanner.start()),
                            icon: const Icon(Icons.refresh),
                            label: const Text('重试相机'),
                          ),
                          OutlinedButton.icon(
                            onPressed: _scanFromGallery,
                            icon: const Icon(Icons.photo_library_outlined),
                            label: const Text('相册识图'),
                          ),
                          OutlinedButton.icon(
                            onPressed: () => _tabs.animateTo(1),
                            icon: const Icon(Icons.edit_note),
                            label: const Text('粘贴/手动'),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
            );
          },
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
            color: Colors.black.withValues(alpha: 0.65),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text(
                  '对准电脑端 AntigravityGoBridge 显示的二维码，自动写入 LAN / Tailscale / Cloudflare 全通道',
                  style: TextStyle(color: Colors.white),
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 10),
                TextButton.icon(
                  onPressed: _pairing ? null : _scanFromGallery,
                  style: TextButton.styleFrom(foregroundColor: Colors.white),
                  icon: const Icon(Icons.photo_library_outlined, size: 18),
                  label: const Text('或从相册选择二维码截图'),
                ),
              ],
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
