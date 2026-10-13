import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:http/http.dart' as http;
import 'package:file_picker/file_picker.dart';
import 'package:http_parser/http_parser.dart';
import 'package:file_saver/file_saver.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

void main() => runApp(const VoxisApp());

class VoxisApp extends StatefulWidget {
  const VoxisApp({super.key});

  @override
  State<VoxisApp> createState() => _VoxisAppState();
}

class _VoxisAppState extends State<VoxisApp> {
  ThemeMode _themeMode = ThemeMode.dark;

  ThemeData _glassTheme(Brightness brightness) {
    final dark = brightness == Brightness.dark;
    final scheme = ColorScheme.fromSeed(
      seedColor: const Color(0xFF8B78FF),
      brightness: brightness,
      surface: dark ? const Color(0xFF11182B) : const Color(0xFFF4F6FC),
    );
    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: scheme,
      scaffoldBackgroundColor:
          dark ? const Color(0xFF090E1B) : const Color(0xFFF1F4FB),
      appBarTheme: AppBarTheme(
        backgroundColor:
            dark ? const Color(0xCC11182B) : const Color(0xDDFBFCFF),
        foregroundColor: scheme.onSurface,
        elevation: 0,
        centerTitle: false,
      ),
      cardTheme: CardThemeData(
        color: dark ? const Color(0xE61A2340) : const Color(0xE6FFFFFF),
        elevation: dark ? 0 : 2,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
          side: BorderSide(
            color: dark ? const Color(0x443E4C77) : const Color(0xFFDDE3F2),
          ),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: dark ? const Color(0xAA171F35) : const Color(0xCCFFFFFF),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide(
            color: dark ? const Color(0x443E4C77) : const Color(0xFFDDE3F2),
          ),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide(
            color: dark ? const Color(0x443E4C77) : const Color(0xFFDDE3F2),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Voxis',
        debugShowCheckedModeBanner: false,
        theme: _glassTheme(Brightness.light),
        darkTheme: _glassTheme(Brightness.dark),
        themeMode: _themeMode,
        home: VoxisHome(
          isDark: _themeMode == ThemeMode.dark,
          onToggleTheme: () => setState(() {
            _themeMode =
                _themeMode == ThemeMode.dark ? ThemeMode.light : ThemeMode.dark;
          }),
        ),
      );
}

class VoxisHome extends StatefulWidget {
  const VoxisHome({
    super.key,
    required this.isDark,
    required this.onToggleTheme,
  });

  final bool isDark;
  final VoidCallback onToggleTheme;

  @override
  State<VoxisHome> createState() => _VoxisHomeState();
}

class _VoxisHomeState extends State<VoxisHome> {
  static const _secureStorage = FlutterSecureStorage();
  StreamSubscription<String>? _pushTokenSubscription;
  bool _pushInitialized = false;
  final _server = TextEditingController(text: 'http://10.0.2.2:8000');
  final _username = TextEditingController();
  final _password = TextEditingController();
  final _message = TextEditingController();

  bool _registerMode = true;
  bool _busy = false;
  String? _token;
  String? _myUsername;
  String? _peer;
  String _status = 'Not connected';
  List<Map<String, dynamic>> _users = [];
  List<Map<String, dynamic>> _messages = [];
  final Set<int> _readMarkedIds = {};
  final Map<int, Uint8List> _attachmentCache = {};
  final Map<int, Future<Uint8List?>> _attachmentRequests = {};
  String _displayName = '';
  String _bio = '';
  String _chatSearch = '';
  String? _avatarUrl;
  bool _uploading = false;
  final ScrollController _chatScroll = ScrollController();
  Timer? _pollTimer;
  Timer? _typingTimer;
  WebSocketChannel? _channel;
  bool _peerTyping = false;
  final Map<String, bool> _onlineUsers = {};

  String _absoluteUrl(String? path) {
    if (path == null || path.isEmpty) return '';
    if (path.startsWith('http://') || path.startsWith('https://')) return path;
    return '$_baseUrl${path.startsWith('/') ? path : '/$path'}';
  }

  Widget _avatar(Map<String, dynamic>? user, {double radius = 22}) {
    final name = user?['display_name']?.toString() ?? user?['username']?.toString() ?? '?';
    final url = _absoluteUrl(user?['avatar_url']?.toString());
    return CircleAvatar(
      radius: radius,
      backgroundImage: url.isNotEmpty ? NetworkImage(url) : null,
      child: url.isEmpty ? Text(name.isEmpty ? '?' : name.characters.first.toUpperCase()) : null,
    );
  }

  String get _baseUrl {
    var value = _server.text.trim().replaceAll(RegExp(r'/+$'), '');
    if (!value.startsWith('http://') && !value.startsWith('https://')) {
      value = 'http://$value';
    }
    return value;
  }

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (_token != null) 'Authorization': 'Bearer $_token',
      };

  @override
  void initState() {
    super.initState();
    _restoreSession();
  }

  @override
  void dispose() {
    _pushTokenSubscription?.cancel();
    _pollTimer?.cancel();
    _typingTimer?.cancel();
    _channel?.sink.close();
    _chatScroll.dispose();
    _server.dispose();
    _username.dispose();
    _password.dispose();
    _message.dispose();
    super.dispose();
  }


  Future<void> _restoreSession() async {
    try {
      final token = await _secureStorage.read(key: 'session_token');
      final username = await _secureStorage.read(key: 'session_username');
      final server = await _secureStorage.read(key: 'server_url');
      if (server != null && server.trim().isNotEmpty) _server.text = server;
      if (token == null || username == null || !mounted) return;
      setState(() {
        _token = token;
        _myUsername = username;
        _username.text = username;
        _status = 'Restoring session…';
      });
      final response = await http.get(Uri.parse('$_baseUrl/api/me'), headers: _headers)
          .timeout(const Duration(seconds: 12));
      if (response.statusCode != 200) {
        await _secureStorage.delete(key: 'session_token');
        if (mounted) setState(() { _token = null; _myUsername = null; _status = 'Please log in again'; });
        return;
      }
      if (!mounted) return;
      setState(() => _status = 'Connected as $username');
      await _connectRealtime();
      await _loadUsers();
      await _registerPushToken();
      _startPolling();
    } catch (_) {
      if (mounted) setState(() => _status = 'Offline — sign in to reconnect');
    }
  }

  void _startPolling() {
    _pollTimer?.cancel();
    _pollTimer = Timer.periodic(const Duration(seconds: 4), (_) {
      if (mounted && _token != null && !_busy) _loadHistory(silent: true);
    });
  }

  Future<void> _registerPushToken() async {
    if (_token == null || _myUsername == null) return;
    const apiKey = String.fromEnvironment('VOXIS_FIREBASE_API_KEY');
    const appId = String.fromEnvironment('VOXIS_FIREBASE_APP_ID');
    const senderId = String.fromEnvironment('VOXIS_FIREBASE_SENDER_ID');
    const projectId = String.fromEnvironment('VOXIS_FIREBASE_PROJECT_ID');
    if ([apiKey, appId, senderId, projectId].any((v) => v.isEmpty)) return;
    try {
      if (!_pushInitialized) {
        await Firebase.initializeApp(options: FirebaseOptions(
          apiKey: apiKey, appId: appId, messagingSenderId: senderId, projectId: projectId,
        ));
        _pushInitialized = true;
      }
      final messaging = FirebaseMessaging.instance;
      await messaging.requestPermission(alert: true, badge: true, sound: true);
      final token = await messaging.getToken();
      if (token != null) await _sendPushToken(token);
      await _pushTokenSubscription?.cancel();
      _pushTokenSubscription = messaging.onTokenRefresh.listen((token) {
        _sendPushToken(token);
      });
    } catch (_) {
      // Push is optional: authentication and chat must still work without Firebase config.
    }
  }

  Future<void> _sendPushToken(String pushToken) async {
    if (_token == null || _myUsername == null) return;
    try {
      await http.post(Uri.parse('$_baseUrl/api/devices'), headers: _headers,
        body: jsonEncode({
          'id': 'android-${_myUsername!.toLowerCase()}',
          'platform': 'android',
          'push_token': pushToken,
        })).timeout(const Duration(seconds: 12));
    } catch (_) {}
  }

  void _showError(Object error) {
    if (!mounted) return;
    final text = error is TimeoutException
        ? 'Сервер не ответил за 15 секунд. Проверь URL сервера, что API запущен, и что телефон и компьютер в одной Wi-Fi сети.'
        : error.toString().replaceFirst('Exception: ', '');
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(text, maxLines: 4)));
  }

  String _apiError(http.Response response) {
    try {
      final data = jsonDecode(response.body);
      final detail = data is Map ? data['detail'] : null;
      if (detail is String) return detail;
      if (detail != null) return detail.toString();
    } catch (_) {}
    return 'HTTP ${response.statusCode}: ${response.reasonPhrase ?? 'request failed'}';
  }

  Future<void> _authenticate() async {
    FocusScope.of(context).unfocus();
    final name = _username.text.trim();
    final password = _password.text;
    if (name.length < 3) {
      _showError('Username must contain at least 3 characters.');
      return;
    }
    if (password.isEmpty) {
      _showError('Enter your password.');
      return;
    }
    if (_registerMode && password.length < 10) {
      _showError('For registration, use a password of at least 10 characters.');
      return;
    }

    setState(() {
      _busy = true;
      _status = 'Connecting…';
    });
    try {
      final response = await http
          .post(
            Uri.parse('$_baseUrl/api/${_registerMode ? 'register' : 'login'}'),
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode({
              'username': name,
              'password': password,
              'device_id': 'android-${name.toLowerCase()}',
              'platform': 'android',
            }),
          )
          .timeout(const Duration(seconds: 15));
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw Exception(_apiError(response));
      }
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _token = data['token'] as String;
        _myUsername = data['username'] as String;
        _status = 'Connected as ${data['username']}';
        _password.clear();
      });
      await _secureStorage.write(key: 'session_token', value: _token);
      await _secureStorage.write(key: 'session_username', value: _myUsername);
      await _secureStorage.write(key: 'server_url', value: _baseUrl);
      await _connectRealtime();
      await _loadUsers();
      await _registerPushToken();
      _startPolling();
    } catch (error) {
      if (mounted) {
        setState(() => _status = 'Connection failed');
      }
      _showError(error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _connectRealtime() async {
    final token = _token;
    if (token == null) return;
    try {
      await _channel?.sink.close();
      final base = Uri.parse(_baseUrl);
      final socketUri = base.replace(
        scheme: base.scheme == 'https' ? 'wss' : 'ws',
        path: '/ws',
        queryParameters: {'token': token},
      );
      final channel = WebSocketChannel.connect(socketUri);
      _channel = channel;
      await channel.ready.timeout(const Duration(seconds: 8));
      channel.stream.listen((event) {
        try {
          final data = jsonDecode(event.toString()) as Map<String, dynamic>;
          final type = data['type'];
          if (type == 'presence') {
            final username = data['username']?.toString();
            if (username != null && mounted) {
              setState(() => _onlineUsers[username] = data['online'] == true);
            }
          } else if (type == 'typing' && data['from'] == _peer) {
            if (mounted) setState(() => _peerTyping = data['active'] == true);
            _typingTimer?.cancel();
            if (data['active'] == true) {
              _typingTimer = Timer(const Duration(seconds: 3), () {
                if (mounted) setState(() => _peerTyping = false);
              });
            }
          } else if (type == 'message' || type == 'message_edited' || type == 'message_deleted') {
            final sender = data['message'] is Map ? data['message']['sender']?.toString() : null;
            if (type != 'message' || sender == _peer || data['message']?['recipient'] == _myUsername) {
              _loadHistory(silent: true);
            }
          }
        } catch (_) {}
      }, onError: (_) {}, onDone: () {});
    } catch (_) {
      // REST polling remains available when realtime connectivity is unavailable.
    }
  }

  void _sendTyping(String value) {
    final peer = _peer;
    final channel = _channel;
    if (peer == null || channel == null || _token == null) return;
    try {
      channel.sink.add(jsonEncode({'type': 'typing', 'to': peer, 'active': value.trim().isNotEmpty}));
    } catch (_) {}
  }

  Future<void> _loadUsers() async {
    if (_token == null) return;
    try {
      final response = await http
          .get(Uri.parse('$_baseUrl/api/users'), headers: _headers)
          .timeout(const Duration(seconds: 15));
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw Exception(_apiError(response));
      }
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final users = (data['users'] as List)
          .map((item) => Map<String, dynamic>.from(item as Map))
          .toList();
      if (!mounted) return;
      setState(() {
        _users = users;
        if (_peer == null || !users.any((u) => u['username'] == _peer)) {
          _peer = users.isEmpty ? null : users.first['username'] as String;
        }
        _status = users.isEmpty
            ? 'Connected · create another account to start chatting'
            : 'Connected as $_myUsername';
      });
      if (_peer != null) await _loadHistory();
    } catch (error) {
      _showError(error);
    }
  }

  Future<void> _selectPeer(String? peer) async {
    if (peer == null) return;
    setState(() {
      _peer = peer;
      _peerTyping = false;
      _messages = [];
    });
    await _loadHistory();
  }

  Future<void> _loadHistory({bool silent = false}) async {
    final peer = _peer;
    if (_token == null || peer == null) return;
    try {
      final response = await http
          .get(Uri.parse('$_baseUrl/api/messages/${Uri.encodeComponent(peer)}'),
              headers: _headers)
          .timeout(const Duration(seconds: 15));
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw Exception(_apiError(response));
      }
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final messages = (data['messages'] as List)
          .map((item) => Map<String, dynamic>.from(item as Map))
          .toList();
      for (final item in messages) {
        final id = item['id'];
        if (item['sender'] != _myUsername && id is int && !_readMarkedIds.contains(id)) {
          try {
            final receipt = await http.post(
              Uri.parse('$_baseUrl/api/messages/$id/read'),
              headers: _headers,
            ).timeout(const Duration(seconds: 8));
            if (receipt.statusCode >= 200 && receipt.statusCode < 300) _readMarkedIds.add(id);
          } catch (_) {}
        }
      }
      if (mounted && _peer == peer) {
        final changed = jsonEncode(_messages) != jsonEncode(messages);
        if (changed) {
          setState(() => _messages = messages);
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (_chatScroll.hasClients) {
              _chatScroll.animateTo(
                _chatScroll.position.maxScrollExtent,
                duration: const Duration(milliseconds: 220),
                curve: Curves.easeOut,
              );
            }
          });
        }
      }
    } catch (error) {
      if (!silent) _showError(error);
    }
  }

  String _displayTime(dynamic raw) {
    if (raw == null) return '';
    final parsed = DateTime.tryParse(raw.toString());
    if (parsed == null) return '';
    final local = parsed.toLocal();
    final hour = local.hour.toString().padLeft(2, '0');
    final minute = local.minute.toString().padLeft(2, '0');
    return '$hour:$minute';
  }

  Future<void> _sendMessage() async {
    final body = _message.text.trim();
    final peer = _peer;
    if (body.isEmpty || peer == null || _token == null || _busy) return;
    setState(() => _busy = true);
    try {
      final response = await http
          .post(
            Uri.parse('$_baseUrl/api/messages'),
            headers: _headers,
            body: jsonEncode({'recipient': peer, 'body': body}),
          )
          .timeout(const Duration(seconds: 15));
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw Exception(_apiError(response));
      }
      _message.clear();
      await _loadHistory();
    } catch (error) {
      _showError(error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _openProfileSettings() async {
    if (_token == null) return;
    try {
      final response = await http.get(Uri.parse('$_baseUrl/api/me'), headers: _headers).timeout(const Duration(seconds: 15));
      if (response.statusCode < 200 || response.statusCode >= 300) throw Exception(_apiError(response));
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _displayName = data['display_name']?.toString() ?? _myUsername ?? '';
        _bio = data['bio']?.toString() ?? '';
        _avatarUrl = data['avatar_url']?.toString();
      });
      final nameController = TextEditingController(text: _displayName);
      final bioController = TextEditingController(text: _bio);
      final saved = await showDialog<bool>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: const Text('Profile & settings'),
          content: SingleChildScrollView(child: Column(mainAxisSize: MainAxisSize.min, children: [
            CircleAvatar(
              radius: 34,
              backgroundImage: _absoluteUrl(_avatarUrl).isNotEmpty ? NetworkImage(_absoluteUrl(_avatarUrl)) : null,
              child: _absoluteUrl(_avatarUrl).isEmpty
                  ? Text((_displayName.isEmpty ? _myUsername ?? '?' : _displayName)[0].toUpperCase(), style: const TextStyle(fontSize: 24))
                  : null,
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(onPressed: () async {
              final file = await FilePicker.pickFile(type: FileType.image);
              if (file == null) return;
              final bytes = await file.readAsBytes();
              final req = http.MultipartRequest('POST', Uri.parse('$_baseUrl/api/avatar'))
                ..headers.addAll({'Authorization': 'Bearer $_token'})
                ..files.add(http.MultipartFile.fromBytes('file', bytes, filename: file.name, contentType: _mediaType(file.name)));
              final streamed = await req.send().timeout(const Duration(seconds: 30));
              final res = await http.Response.fromStream(streamed);
              if (res.statusCode < 200 || res.statusCode >= 300) {
                if (mounted) _showError(Exception(_apiError(res)));
              } else if (mounted) {
                setState(() {
                  _avatarUrl = '$_baseUrl/api/avatar/${Uri.encodeComponent(_myUsername ?? '')}?v=${DateTime.now().millisecondsSinceEpoch}';
                });
                await _loadUsers();
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Avatar updated')));
              }
            }, icon: const Icon(Icons.photo_camera_outlined), label: const Text('Change avatar')),
            const SizedBox(height: 12),
            TextField(controller: nameController, maxLength: 60, decoration: const InputDecoration(labelText: 'Display name')),
            const SizedBox(height: 8),
            TextField(controller: bioController, maxLength: 300, maxLines: 3, decoration: const InputDecoration(labelText: 'About me')),
            const SizedBox(height: 8),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Dark theme'),
              value: widget.isDark,
              onChanged: (_) => widget.onToggleTheme(),
            ),
            Text('Account: @$_myUsername', style: Theme.of(context).textTheme.bodySmall),
            Text('Server: $_baseUrl', style: Theme.of(context).textTheme.bodySmall),
          ])),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Save profile')),
          ],
        ),
      );
      if (saved == true) {
        final res = await http.patch(Uri.parse('$_baseUrl/api/profile'), headers: _headers,
          body: jsonEncode({'display_name': nameController.text.trim(), 'bio': bioController.text.trim()})).timeout(const Duration(seconds: 15));
        if (res.statusCode < 200 || res.statusCode >= 300) throw Exception(_apiError(res));
        if (mounted) setState(() { _displayName = nameController.text.trim(); _bio = bioController.text.trim(); });
        await _loadUsers();
        if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Profile saved')));
      }
      nameController.dispose();
      bioController.dispose();
    } catch (error) { _showError(error); }
  }

  MediaType _mediaType(String name) {
    final lower = name.toLowerCase();
    if (lower.endsWith('.png')) return MediaType('image', 'png');
    if (lower.endsWith('.jpg') || lower.endsWith('.jpeg')) return MediaType('image', 'jpeg');
    if (lower.endsWith('.webp')) return MediaType('image', 'webp');
    if (lower.endsWith('.pdf')) return MediaType('application', 'pdf');
    return MediaType('application', 'octet-stream');
  }

  Future<Uint8List?> _fetchAttachmentBytes(int id) {
    if (_attachmentCache.containsKey(id)) return Future.value(_attachmentCache[id]);
    return _attachmentRequests.putIfAbsent(id, () async {
      try {
        final response = await http.get(
          Uri.parse('$_baseUrl/api/attachments/$id'),
          headers: {'Authorization': 'Bearer $_token'},
        ).timeout(const Duration(seconds: 60));
        if (response.statusCode < 200 || response.statusCode >= 300) return null;
        _attachmentCache[id] = response.bodyBytes;
        return response.bodyBytes;
      } catch (_) {
        return null;
      } finally {
        _attachmentRequests.remove(id);
      }
    });
  }

  Future<void> _downloadAttachment(dynamic id, String name) async {
    if (_token == null || id == null) return;
    try {
      final bytes = await _fetchAttachmentBytes(id is int ? id : int.parse(id.toString()));
      if (bytes == null) throw Exception('Could not download this attachment');
      await FileSaver.instance.saveFile(
        name: name.isEmpty ? 'attachment' : name,
        bytes: bytes,
      );
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Saved: $name')),
        );
      }
    } catch (error) {
      _showError(error);
    }
  }

  Future<void> _pickAndSendFile() async {
    if (_token == null || _peer == null || _uploading || _busy) return;
    final file = await FilePicker.pickFile();
    if (file == null) return;
    final fileSize = file.lengthSync() ?? await file.length();
    if ((fileSize ?? 0) > 20 * 1024 * 1024) { _showError(Exception('Maximum file size is 20 MB')); return; }
    final bytes = await file.readAsBytes();
    setState(() => _uploading = true);
    try {
      final req = http.MultipartRequest('POST', Uri.parse('$_baseUrl/api/attachments'))
        ..headers.addAll({'Authorization': 'Bearer $_token'})
        ..files.add(http.MultipartFile.fromBytes('file', bytes, filename: file.name, contentType: _mediaType(file.name)));
      final streamed = await req.send().timeout(const Duration(seconds: 60));
      final response = await http.Response.fromStream(streamed);
      if (response.statusCode < 200 || response.statusCode >= 300) throw Exception(_apiError(response));
      final uploaded = jsonDecode(response.body) as Map<String, dynamic>;
      final sent = await http.post(Uri.parse('$_baseUrl/api/messages'), headers: _headers,
        body: jsonEncode({'recipient': _peer, 'body': '', 'attachment_id': uploaded['id']})).timeout(const Duration(seconds: 30));
      if (sent.statusCode < 200 || sent.statusCode >= 300) throw Exception(_apiError(sent));
      await _loadHistory();
    } catch (error) { _showError(error); }
    finally { if (mounted) setState(() => _uploading = false); }
  }

  Future<void> _messageActions(Map<String, dynamic> message, bool mine) async {
    final id = message['id'];
    if (id == null || _token == null) return;
    final action = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(leading: const Icon(Icons.add_reaction_outlined), title: const Text('React'), onTap: () => Navigator.pop(ctx, 'react')),
          ListTile(leading: const Icon(Icons.push_pin_outlined), title: const Text('Pin for me'), onTap: () => Navigator.pop(ctx, 'pin')),
          if (mine) ListTile(leading: const Icon(Icons.edit_outlined), title: const Text('Edit message'), onTap: () => Navigator.pop(ctx, 'edit')),
          if (mine) ListTile(leading: const Icon(Icons.delete_outline, color: Colors.red), title: const Text('Delete message'), onTap: () => Navigator.pop(ctx, 'delete')),
        ]),
      ),
    );
    if (action == null) return;
    try {
      http.Response response;
      if (action == 'react') {
        final emoji = await showDialog<String>(
          context: context,
          builder: (ctx) => SimpleDialog(
            title: const Text('Choose reaction'),
            children: ['👍','❤️','😂','😮','😢','🔥'].map((emoji) => SimpleDialogOption(
              onPressed: () => Navigator.pop(ctx, emoji),
              child: Text(emoji, style: const TextStyle(fontSize: 28)),
            )).toList(),
          ),
        );
        if (emoji == null) return;
        response = await http.post(Uri.parse('$_baseUrl/api/messages/$id/reactions'), headers: _headers, body: jsonEncode({'reaction': emoji}));
      } else if (action == 'pin') {
        response = await http.post(Uri.parse('$_baseUrl/api/messages/$id/pin'), headers: _headers);
      } else if (action == 'edit') {
        final controller = TextEditingController(text: message['body']?.toString() ?? '');
        final edited = await showDialog<String>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: const Text('Edit message'),
            content: TextField(controller: controller, autofocus: true, maxLength: 4000, minLines: 1, maxLines: 5),
            actions: [
              TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
              FilledButton(onPressed: () => Navigator.pop(ctx, controller.text.trim()), child: const Text('Save')),
            ],
          ),
        );
        controller.dispose();
        if (edited == null || edited.isEmpty) return;
        response = await http.patch(Uri.parse('$_baseUrl/api/messages/$id'), headers: _headers, body: jsonEncode({'body': edited}));
      } else {
        final confirm = await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: const Text('Delete message?'),
            content: const Text('This will remove the message for everyone in the conversation.'),
            actions: [
              TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
              FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Delete')),
            ],
          ),
        );
        if (confirm != true) return;
        response = await http.delete(Uri.parse('$_baseUrl/api/messages/$id'), headers: _headers);
      }
      if (response.statusCode < 200 || response.statusCode >= 300) throw Exception(_apiError(response));
      await _loadHistory();
    } catch (error) {
      _showError(error);
    }
  }

  Future<void> _searchMessages() async {
    if (_token == null) return;
    final controller = TextEditingController();
    final query = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Search messages'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(hintText: 'Search in your chats', prefixIcon: Icon(Icons.search)),
          onSubmitted: (_) => Navigator.pop(ctx, controller.text.trim()),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, controller.text.trim()), child: const Text('Search')),
        ],
      ),
    );
    controller.dispose();
    if (query == null || query.trim().isEmpty) return;
    try {
      final response = await http.get(
        Uri.parse('$_baseUrl/api/search/messages?q=${Uri.encodeQueryComponent(query.trim())}'),
        headers: _headers,
      ).timeout(const Duration(seconds: 20));
      if (response.statusCode < 200 || response.statusCode >= 300) throw Exception(_apiError(response));
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final results = (data['messages'] as List).cast<Map<String, dynamic>>();
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text('Results: ${results.length}'),
          content: SizedBox(
            width: 420,
            child: results.isEmpty
                ? const Text('No messages found.')
                : ListView.separated(
                    shrinkWrap: true,
                    itemCount: results.length,
                    separatorBuilder: (_, __) => const Divider(),
                    itemBuilder: (_, i) {
                      final item = results[i];
                      final peer = item['sender'] == _myUsername ? item['recipient'] : item['sender'];
                      return ListTile(
                        leading: const Icon(Icons.chat_bubble_outline),
                        title: Text(item['body']?.toString().isNotEmpty == true ? item['body'].toString() : (item['attachment_name']?.toString() ?? 'Attachment'), maxLines: 3, overflow: TextOverflow.ellipsis),
                        subtitle: Text('@$peer · ${_displayTime(item['created_at'])}'),
                        onTap: () {
                          Navigator.pop(ctx);
                          if (peer is String && _users.any((u) => u['username'] == peer)) _selectPeer(peer);
                        },
                      );
                    },
                  ),
          ),
          actions: [TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Close'))],
        ),
      );
    } catch (error) {
      _showError(error);
    }
  }

  Future<void> _blockCurrentPeer() async {
    final peer = _peer;
    if (_token == null || peer == null) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Block @$peer?'),
        content: const Text('They will not be able to exchange messages with you while blocked.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Block')),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      final response = await http.post(Uri.parse('$_baseUrl/api/blocks'), headers: _headers, body: jsonEncode({'username': peer})).timeout(const Duration(seconds: 15));
      if (response.statusCode < 200 || response.statusCode >= 300) throw Exception(_apiError(response));
      if (!mounted) return;
      setState(() { _peer = null; _messages = []; });
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('@$peer blocked')));
    } catch (error) {
      _showError(error);
    }
  }

  Future<void> _logout() async {
    try {
      if (_token != null) {
        await http.post(Uri.parse('$_baseUrl/api/logout'), headers: _headers);
      }
    } catch (_) {}
    if (!mounted) return;
    _pollTimer?.cancel();
    _pollTimer = null;
    _typingTimer?.cancel();
    _typingTimer = null;
    await _channel?.sink.close();
    _channel = null;
    _onlineUsers.clear();
    await _secureStorage.delete(key: 'session_token');
    await _secureStorage.delete(key: 'session_username');
    setState(() {
      _peerTyping = false;
      _token = null;
      _myUsername = null;
      _peer = null;
      _users = [];
      _messages = [];
      _status = 'Not connected';
    });
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          title: const Row(children: [
            Icon(Icons.forum_rounded),
            SizedBox(width: 10),
            Text('Voxis'),
          ]),
          actions: [
            IconButton(
              tooltip: widget.isDark ? 'Switch to light theme' : 'Switch to dark theme',
              onPressed: widget.onToggleTheme,
              icon: Icon(widget.isDark
                  ? Icons.light_mode_rounded
                  : Icons.dark_mode_rounded),
            ),
            if (_token != null)
              IconButton(
                tooltip: 'Profile and settings',
                onPressed: _openProfileSettings,
                icon: const Icon(Icons.account_circle_outlined),
              ),
            if (_token != null)
              IconButton(
                tooltip: 'Refresh users and history',
                onPressed: _busy ? null : _loadUsers,
                icon: const Icon(Icons.refresh),
              ),
            if (_token != null)
              IconButton(
                tooltip: 'Log out',
                onPressed: _logout,
                icon: const Icon(Icons.logout),
              ),
          ],
        ),
        body: _token == null ? _buildAuth() : _buildChat(),
      );

  Widget _buildAuth() => Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 28),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 430),
            child: Card(
              clipBehavior: Clip.antiAlias,
              child: Padding(
                padding: const EdgeInsets.fromLTRB(24, 30, 24, 22),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Center(
                      child: Container(
                        width: 76,
                        height: 76,
                        decoration: BoxDecoration(
                          gradient: const LinearGradient(
                            colors: [Color(0xFF8B78FF), Color(0xFF5A8CFF)],
                            begin: Alignment.topLeft,
                            end: Alignment.bottomRight,
                          ),
                          borderRadius: BorderRadius.circular(25),
                        ),
                        child: const Icon(Icons.forum_rounded, color: Colors.white, size: 39),
                      ),
                    ),
                    const SizedBox(height: 20),
                    Text(
                      _registerMode ? 'Join Voxis' : 'Welcome back',
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.w800),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      _registerMode
                          ? 'Create an account to start private conversations.'
                          : 'Sign in to continue your conversations.',
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                    ),
                    const SizedBox(height: 26),
                    TextField(
                      controller: _username,
                      textInputAction: TextInputAction.next,
                      autocorrect: false,
                      textCapitalization: TextCapitalization.none,
                      decoration: const InputDecoration(
                        labelText: 'Username',
                        hintText: 'your_username',
                        helperText: '3–24 letters, numbers, dot, dash or underscore',
                        prefixIcon: Icon(Icons.person_outline_rounded),
                      ),
                    ),
                    const SizedBox(height: 14),
                    TextField(
                      controller: _password,
                      obscureText: true,
                      enableSuggestions: false,
                      autocorrect: false,
                      onSubmitted: (_) => _authenticate(),
                      decoration: InputDecoration(
                        labelText: 'Password',
                        helperText: _registerMode ? 'At least 10 characters' : null,
                        prefixIcon: const Icon(Icons.lock_outline_rounded),
                      ),
                    ),
                    const SizedBox(height: 18),
                    FilledButton(
                      onPressed: _busy ? null : _authenticate,
                      style: FilledButton.styleFrom(
                        minimumSize: const Size.fromHeight(52),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(15)),
                      ),
                      child: _busy
                          ? const SizedBox(width: 21, height: 21, child: CircularProgressIndicator(strokeWidth: 2))
                          : Text(_registerMode ? 'Create account' : 'Log in'),
                    ),
                    const SizedBox(height: 8),
                    TextButton(
                      onPressed: _busy ? null : () => setState(() => _registerMode = !_registerMode),
                      child: Text(_registerMode
                          ? 'Already have an account? Log in'
                          : 'New to Voxis? Create an account'),
                    ),
                    const Divider(height: 28),
                    ExpansionTile(
                      tilePadding: EdgeInsets.zero,
                      childrenPadding: const EdgeInsets.only(bottom: 8),
                      leading: const Icon(Icons.dns_outlined),
                      title: const Text('Connection settings'),
                      subtitle: Text(_baseUrl, maxLines: 1, overflow: TextOverflow.ellipsis),
                      children: [
                        TextField(
                          controller: _server,
                          keyboardType: TextInputType.url,
                          autocorrect: false,
                          decoration: const InputDecoration(
                            labelText: 'API server URL',
                            hintText: 'http://192.168.1.10:8000',
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Text(
                      _status,
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      );

  Widget _buildChat() {
    final peer = _peer;
    final peerMatches = _users.where((u) => u['username'] == peer).toList();
    final peerData = peerMatches.isEmpty ? null : peerMatches.first;
    final displayName = peerData?['display_name']?.toString() ?? peer ?? 'Voxis';
    return Column(
      children: [
        Container(
          padding: const EdgeInsets.fromLTRB(16, 10, 12, 10),
          decoration: BoxDecoration(
            color: Theme.of(context).colorScheme.surface.withValues(alpha: 0.92),
            border: Border(
              bottom: BorderSide(
                color: Theme.of(context).colorScheme.outlineVariant.withValues(alpha: 0.45),
              ),
            ),
          ),
          child: Row(
            children: [
              _avatar(peerData == null ? null : Map<String, dynamic>.from(peerData), radius: 23),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(displayName, maxLines: 1, overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
                    const SizedBox(height: 3),
                    Text(
                      peer == null ? 'Choose a conversation' : _peerTyping ? 'typing…' : (_onlineUsers[peer] == true ? 'online' : '@$peer · Voxis'),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                ),
              ),
              IconButton(
                tooltip: 'Refresh messages',
                onPressed: _busy ? null : () => _loadHistory(),
                icon: const Icon(Icons.refresh_rounded),
              ),
              PopupMenuButton<String>(
                tooltip: 'Conversation options',
                onSelected: (value) {
                  if (value == 'users') _loadUsers();
                  if (value == 'profile') _openProfileSettings();
                  if (value == 'search') _searchMessages();
                  if (value == 'block') _blockCurrentPeer();
                  if (value == 'logout') _logout();
                },
                itemBuilder: (_) => [
                  const PopupMenuItem(value: 'users', child: ListTile(
                    leading: Icon(Icons.people_outline), title: Text('Refresh contacts'),
                    contentPadding: EdgeInsets.zero, dense: true)),
                  const PopupMenuItem(value: 'profile', child: ListTile(
                    leading: Icon(Icons.account_circle_outlined), title: Text('Profile & settings'),
                    contentPadding: EdgeInsets.zero, dense: true)),
                  const PopupMenuItem(value: 'search', child: ListTile(
                    leading: Icon(Icons.search_rounded), title: Text('Search messages'),
                    contentPadding: EdgeInsets.zero, dense: true)),
                  PopupMenuItem(value: 'block', enabled: _peer != null, child: ListTile(
                    leading: Icon(Icons.block_rounded), title: Text('Block this contact'),
                    contentPadding: EdgeInsets.zero, dense: true)),
                  const PopupMenuItem(value: 'logout', child: ListTile(
                    leading: Icon(Icons.logout_rounded), title: Text('Log out'),
                    contentPadding: EdgeInsets.zero, dense: true)),
                ],
              ),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 0),
          child: TextField(
            onChanged: (value) => setState(() => _chatSearch = value.trim().toLowerCase()),
            decoration: const InputDecoration(
              hintText: 'Search contacts…',
              prefixIcon: Icon(Icons.search_rounded),
              isDense: true,
            ),
          ),
        ),
        if (_users.length > 1)
          SizedBox(
            height: 56,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
              children: _users.where((user) {
                final username = user['username']?.toString().toLowerCase() ?? '';
                final name = user['display_name']?.toString().toLowerCase() ?? '';
                return username.contains(_chatSearch) || name.contains(_chatSearch);
              }).map((user) {
                final username = user['username'] as String;
                final selected = username == _peer;
                final name = user['display_name']?.toString() ?? username;
                return Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    selected: selected,
                    avatar: _avatar(Map<String, dynamic>.from(user), radius: 12),
                    label: Text(name, overflow: TextOverflow.ellipsis),
                    onSelected: _busy ? null : (_) => _selectPeer(username),
                  ),
                );
              }).toList(),
            ),
          ),
        Expanded(
          child: _messages.isEmpty
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(28),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Container(
                          width: 76,
                          height: 76,
                          decoration: BoxDecoration(
                            color: Theme.of(context).colorScheme.primaryContainer.withValues(alpha: 0.65),
                            shape: BoxShape.circle,
                          ),
                          child: Icon(
                            peer == null ? Icons.forum_outlined : Icons.waving_hand_rounded,
                            size: 34,
                            color: Theme.of(context).colorScheme.primary,
                          ),
                        ),
                        const SizedBox(height: 18),
                        Text(
                          peer == null ? 'Your conversations start here' : 'Say hello to $displayName',
                          textAlign: TextAlign.center,
                          style: Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700),
                        ),
                        const SizedBox(height: 8),
                        Text(
                          peer == null
                              ? 'Create another account on a second device to start chatting.'
                              : 'No messages yet. Send the first message and start the conversation.',
                          textAlign: TextAlign.center,
                          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                            color: Theme.of(context).colorScheme.onSurfaceVariant,
                          ),
                        ),
                      ],
                    ),
                  ),
                )
              : ListView.builder(
                  controller: _chatScroll,
                  padding: const EdgeInsets.fromLTRB(14, 18, 14, 20),
                  itemCount: _messages.length,
                  itemBuilder: (context, index) {
                    final item = _messages[index];
                    final mine = item['sender'] == _myUsername;
                    final body = item['body']?.toString() ?? '';
                    final attachmentId = item['attachment_id'];
                    final attachmentName = item['attachment_name']?.toString() ?? 'File';
                    final attachmentSize = item['attachment_size'];
                    final attachmentType = item['attachment_content_type']?.toString() ?? '';
                    final time = _displayTime(item['created_at']);
                    final statusMark = mine ? (item['read_at'] != null ? ' ✓✓' : ' ✓') : '';
                    final previous = index > 0 ? _messages[index - 1] : null;
                    final sameSender = previous != null && (previous['sender'] == item['sender']);
                    return Padding(
                      padding: EdgeInsets.only(
                        top: sameSender ? 3 : 10,
                        bottom: 2,
                      ),
                      child: Row(
                        mainAxisAlignment: mine ? MainAxisAlignment.end : MainAxisAlignment.start,
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: [
                          if (!mine && !sameSender) ...[
                            CircleAvatar(
                              radius: 14,
                              backgroundColor: Theme.of(context).colorScheme.secondaryContainer,
                              child: Text(
                                (item['sender']?.toString().isNotEmpty ?? false)
                                    ? item['sender'].toString()[0].toUpperCase() : '?',
                                style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w700),
                              ),
                            ),
                            const SizedBox(width: 7),
                          ] else if (!mine) const SizedBox(width: 21),
                          Flexible(
                            child: GestureDetector(
                              onLongPress: () => _messageActions(item, mine),
                              child: Container(
                              constraints: const BoxConstraints(maxWidth: 340),
                              padding: const EdgeInsets.fromLTRB(13, 9, 12, 7),
                              decoration: BoxDecoration(
                                color: mine
                                    ? Theme.of(context).colorScheme.primary
                                    : Theme.of(context).colorScheme.surfaceContainerHighest,
                                borderRadius: BorderRadius.only(
                                  topLeft: const Radius.circular(18),
                                  topRight: const Radius.circular(18),
                                  bottomLeft: Radius.circular(mine ? 18 : (sameSender ? 18 : 5)),
                                  bottomRight: Radius.circular(mine ? (sameSender ? 18 : 5) : 18),
                                ),
                              ),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  if (!mine && !sameSender) ...[
                                    Text(
                                      item['sender']?.toString() ?? 'Unknown',
                                      style: Theme.of(context).textTheme.labelSmall?.copyWith(
                                        color: Theme.of(context).colorScheme.primary,
                                        fontWeight: FontWeight.w700,
                                      ),
                                    ),
                                    const SizedBox(height: 3),
                                  ],
                                  if (attachmentId != null && attachmentType.startsWith('image/')) ...[
                                    FutureBuilder<Uint8List?>(
                                      future: _fetchAttachmentBytes(attachmentId is int ? attachmentId : int.tryParse(attachmentId.toString()) ?? -1),
                                      builder: (context, snapshot) {
                                        if (!snapshot.hasData) return const Padding(
                                          padding: EdgeInsets.only(bottom: 6),
                                          child: SizedBox(width: 220, height: 120, child: Center(child: CircularProgressIndicator(strokeWidth: 2))),
                                        );
                                        return InkWell(
                                          onTap: () => _downloadAttachment(attachmentId, attachmentName),
                                          child: ClipRRect(
                                            borderRadius: BorderRadius.circular(12),
                                            child: Image.memory(snapshot.data!, width: 240, height: 190, fit: BoxFit.cover),
                                          ),
                                        );
                                      },
                                    ),
                                  ],
                                  if (attachmentId != null) ...[
                                    InkWell(
                                      onTap: () => _downloadAttachment(attachmentId, attachmentName),
                                      borderRadius: BorderRadius.circular(12),
                                      child: Container(
                                      padding: const EdgeInsets.all(10),
                                      margin: const EdgeInsets.only(bottom: 6),
                                      decoration: BoxDecoration(
                                        color: (mine ? Theme.of(context).colorScheme.onPrimary : Theme.of(context).colorScheme.onSurface).withValues(alpha: 0.08),
                                        borderRadius: BorderRadius.circular(12),
                                      ),
                                      child: Row(mainAxisSize: MainAxisSize.min, children: [
                                        const Icon(Icons.insert_drive_file_outlined, size: 24),
                                        const SizedBox(width: 8),
                                        Flexible(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                                          Text(attachmentName, maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600)),
                                          Text(attachmentSize == null ? 'File attachment' : '${(attachmentSize as num) / 1024 < 1024 ? ((attachmentSize as num) / 1024).toStringAsFixed(1) + ' KB' : ((attachmentSize as num) / (1024 * 1024)).toStringAsFixed(1) + ' MB'}',
                                            style: Theme.of(context).textTheme.labelSmall),
                                        ])),
                                      ]),
                                    )),
                                  ],
                                  if (body.isNotEmpty) Text(
                                    body,
                                    style: TextStyle(
                                      height: 1.3,
                                      color: mine
                                          ? Theme.of(context).colorScheme.onPrimary
                                          : Theme.of(context).colorScheme.onSurface,
                                    ),
                                  ),
                                  const SizedBox(height: 3),
                                  Align(
                                    alignment: Alignment.bottomRight,
                                    child: Text(
                                      '$time$statusMark',
                                      style: Theme.of(context).textTheme.labelSmall?.copyWith(
                                        fontSize: 10,
                                        color: mine
                                            ? Theme.of(context).colorScheme.onPrimary.withValues(alpha: 0.72)
                                            : Theme.of(context).colorScheme.onSurfaceVariant,
                                      ),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            ),
                          ),
                          if (mine) const SizedBox(width: 4),
                        ],
                      ),
                    );
                  },
                ),
        ),
        Container(
          padding: const EdgeInsets.fromLTRB(12, 9, 12, 8),
          decoration: BoxDecoration(
            color: Theme.of(context).colorScheme.surface,
            border: Border(
              top: BorderSide(
                color: Theme.of(context).colorScheme.outlineVariant.withValues(alpha: 0.45),
              ),
            ),
          ),
          child: SafeArea(
            top: false,
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                IconButton(
                  tooltip: 'Send a file',
                  onPressed: _peer == null || _uploading || _busy ? null : _pickAndSendFile,
                  icon: Icon(Icons.add_circle_outline_rounded,
                    color: Theme.of(context).colorScheme.onSurfaceVariant),
                ),
                Expanded(
                  child: TextField(
                    controller: _message,
                    enabled: _peer != null && !_busy,
                    minLines: 1,
                    maxLines: 5,
                    textCapitalization: TextCapitalization.sentences,
                    keyboardType: TextInputType.multiline,
                    onChanged: _sendTyping,
                    onSubmitted: (_) => _sendMessage(),
                    decoration: InputDecoration(
                      hintText: _peer == null ? 'Choose a contact first' : 'Message…',
                      contentPadding: const EdgeInsets.symmetric(horizontal: 17, vertical: 12),
                      filled: true,
                      fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.65),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(26),
                        borderSide: BorderSide.none,
                      ),
                      enabledBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(26),
                        borderSide: BorderSide.none,
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                SizedBox(
                  height: 46,
                  width: 46,
                  child: IconButton.filled(
                    tooltip: 'Send message',
                    onPressed: _peer == null || _busy ? null : _sendMessage,
                    icon: _busy
                        ? const SizedBox(width: 18, height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2))
                        : _uploading ? const Icon(Icons.attach_file_rounded) : const Icon(Icons.send_rounded),
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}
