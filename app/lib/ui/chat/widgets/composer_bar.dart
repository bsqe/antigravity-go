import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../../../state/chat_state.dart';

class ComposerBar extends StatefulWidget {
  const ComposerBar({
    super.key,
    required this.isGenerating,
    required this.isUploading,
    required this.pendingImages,
    required this.onAddImage,
    required this.onRemoveImage,
    required this.onSend,
    required this.onCancel,
  });

  final bool isGenerating;
  final bool isUploading;
  final List<PendingImage> pendingImages;
  final void Function(PendingImage image) onAddImage;
  final void Function(int index) onRemoveImage;
  final void Function(String text) onSend;
  final VoidCallback onCancel;

  @override
  State<ComposerBar> createState() => _ComposerBarState();
}

class _ComposerBarState extends State<ComposerBar> {
  final _controller = TextEditingController();
  final _picker = ImagePicker();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _pickImage(ImageSource source) async {
    final picked = await _picker.pickImage(
      source: source,
      maxWidth: 2048,
      imageQuality: 88,
    );
    if (picked == null) return;
    final bytes = await picked.readAsBytes();
    widget.onAddImage(PendingImage(bytes: bytes, filename: picked.name));
  }

  void _showAttachmentMenu() {
    showModalBottomSheet<void>(
      context: context,
      builder: (ctx) => SafeArea(
        child: Wrap(
          children: [
            ListTile(
              leading: const Icon(Icons.photo_library_outlined),
              title: const Text('从相册选择图片'),
              onTap: () {
                Navigator.pop(ctx);
                _pickImage(ImageSource.gallery);
              },
            ),
            ListTile(
              leading: const Icon(Icons.camera_alt_outlined),
              title: const Text('拍照上传'),
              onTap: () {
                Navigator.pop(ctx);
                _pickImage(ImageSource.camera);
              },
            ),
          ],
        ),
      ),
    );
  }

  void _handleSend() {
    final text = _controller.text.trim();
    if (text.isEmpty && widget.pendingImages.isEmpty) return;
    _controller.clear();
    widget.onSend(text);
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.fromLTRB(10, 8, 10, 10),
        decoration: BoxDecoration(
          color: scheme.surface,
          border: Border(top: BorderSide(color: scheme.outlineVariant.withValues(alpha: 0.5))),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (widget.pendingImages.isNotEmpty)
              SizedBox(
                height: 76,
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  itemCount: widget.pendingImages.length,
                  separatorBuilder: (_, _) => const SizedBox(width: 8),
                  itemBuilder: (_, idx) {
                    final img = widget.pendingImages[idx];
                    return Stack(
                      children: [
                        ClipRRect(
                          borderRadius: BorderRadius.circular(10),
                          child: Image.memory(
                            img.bytes,
                            width: 68,
                            height: 68,
                            fit: BoxFit.cover,
                          ),
                        ),
                        Positioned(
                          top: 2,
                          right: 2,
                          child: InkWell(
                            onTap: () => widget.onRemoveImage(idx),
                            child: CircleAvatar(
                              radius: 10,
                              backgroundColor: scheme.scrim.withValues(alpha: 0.7),
                              child: const Icon(Icons.close, size: 12, color: Colors.white),
                            ),
                          ),
                        ),
                      ],
                    );
                  },
                ),
              ),
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                IconButton(
                  tooltip: '添加图片',
                  onPressed: widget.isGenerating || widget.isUploading ? null : _showAttachmentMenu,
                  icon: const Icon(Icons.add_photo_alternate_outlined),
                ),
                Expanded(
                  child: TextField(
                    controller: _controller,
                    minLines: 1,
                    maxLines: 5,
                    textInputAction: TextInputAction.newline,
                    decoration: InputDecoration(
                      hintText: widget.isUploading ? '正在上传图片…' : '给 Antigravity 发消息…',
                      filled: true,
                      fillColor: scheme.surfaceContainerHighest.withValues(alpha: 0.6),
                      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(20),
                        borderSide: BorderSide.none,
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                if (widget.isGenerating)
                  FilledButton.tonalIcon(
                    onPressed: widget.onCancel,
                    style: FilledButton.styleFrom(
                      backgroundColor: scheme.errorContainer,
                      foregroundColor: scheme.onErrorContainer,
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                    ),
                    icon: const Icon(Icons.stop_rounded, size: 20),
                    label: const Text('停止'),
                  )
                else
                  FilledButton(
                    onPressed: widget.isUploading ? null : _handleSend,
                    style: FilledButton.styleFrom(
                      shape: const CircleBorder(),
                      padding: const EdgeInsets.all(12),
                    ),
                    child: widget.isUploading
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.arrow_upward_rounded, size: 20),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
