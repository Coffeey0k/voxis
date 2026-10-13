import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:file_picker/file_picker.dart';
import 'package:http_parser/http_parser.dart';

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
  String _displayName = '';
  String _bio = '';
  String? _avatarUrl;
  bool _uploading = false;
  final ScrollController _chatScroll = ScrollController();
  Timer? _pollTimer;

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
  void dispose() {
    _pollTimer?.cancel();
    _chatScroll.dispose();
    _server.dispose();
    _username.dispose();
    _password.dispose();
    _message.dispose();
    super.dispose();
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
      await _loadUsers();
      _pollTimer?.cancel();
      _pollTimer = Timer.periodic(const Duration(seconds: 4), (_) {
        if (mounted && _token != null && !_busy) _loadHistory(silent: true);
      });
    } catch (error) {
      if (mounted) {
        setState(() => _status = 'Connection failed');
      }
      _showError(error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
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
            CircleAvatar(radius: 34, child: Text((_displayName.isEmpty ? _myUsername ?? '?' : _displayName)[0].toUpperCase(), style: const TextStyle(fontSize: 24))),
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

  Future<void> _logout() async {
    try {
      if (_token != null) {
        await http.post(Uri.parse('$_baseUrl/api/logout'), headers: _headers);
      }
    } catch (_) {}
    if (!mounted) return;
    _pollTimer?.cancel();
    _pollTimer = null;
    setState(() {
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
          padding: const EdgeInsets.all(20),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 480),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Icon(Icons.forum_rounded, size: 64),
                const SizedBox(height: 12),
                Text(
                  _registerMode ? 'Create your Voxis account' : 'Sign in to Voxis',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
                const SizedBox(height: 20),
                TextField(
                  controller: _server,
                  keyboardType: TextInputType.url,
                  decoration: const InputDecoration(
                    labelText: 'API server URL',
                    hintText: 'http://192.168.1.10:8000',
                    prefixIcon: Icon(Icons.dns_outlined),
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _username,
                  textInputAction: TextInputAction.next,
                  autocorrect: false,
                  decoration: const InputDecoration(
                    labelText: 'Username',
                    helperText: '3–24 letters, numbers, dot, dash or underscore',
                    prefixIcon: Icon(Icons.person_outline),
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _password,
                  obscureText: true,
                  onSubmitted: (_) => _authenticate(),
                  decoration: InputDecoration(
                    labelText: 'Password',
                    helperText: _registerMode ? 'At least 10 characters' : null,
                    prefixIcon: const Icon(Icons.lock_outline),
                    border: const OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 16),
                FilledButton(
                  onPressed: _busy ? null : _authenticate,
                  child: Text(_busy
                      ? 'Please wait…'
                      : _registerMode
                          ? 'Register'
                          : 'Log in'),
                ),
                TextButton(
                  onPressed: _busy
                      ? null
                      : () => setState(() => _registerMode = !_registerMode),
                  child: Text(_registerMode
                      ? 'Already registered? Log in'
                      : 'New to Voxis? Create an account'),
                ),
                const SizedBox(height: 8),
                Text(_status, textAlign: TextAlign.center),
                const SizedBox(height: 8),
                const Text(
                  'Android emulator: use http://10.0.2.2:8000. '
                  'On a real phone, enter your computer’s LAN IP and keep both devices on the same Wi-Fi.',
                  textAlign: TextAlign.center,
                ),
              ],
            ),
          ),
        ),
      );

  String _displayTime(dynamic raw) {
    if (raw == null) return '';
    final parsed = DateTime.tryParse(raw.toString())?.toLocal();
    if (parsed == null) return '';
    final hour = parsed.hour.toString().padLeft(2, '0');
    final minute = parsed.minute.toString().padLeft(2, '0');
    return '$hour:$minute';
  }

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
              CircleAvatar(
                radius: 23,
                backgroundColor: Theme.of(context).colorScheme.primaryContainer,
                child: Text(
                  displayName.isEmpty ? '?' : displayName.characters.first.toUpperCase(),
                  style: TextStyle(
                    color: Theme.of(context).colorScheme.onPrimaryContainer,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(displayName, maxLines: 1, overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
                    const SizedBox(height: 3),
                    Text(
                      peer == null ? 'Choose a conversation' : '@$peer · Voxis',
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
                  if (value == 'logout') _logout();
                },
                itemBuilder: (_) => const [
                  PopupMenuItem(value: 'users', child: ListTile(
                    leading: Icon(Icons.people_outline), title: Text('Refresh contacts'),
                    contentPadding: EdgeInsets.zero, dense: true)),
                  PopupMenuItem(value: 'logout', child: ListTile(
                    leading: Icon(Icons.logout_rounded), title: Text('Log out'),
                    contentPadding: EdgeInsets.zero, dense: true)),
                ],
              ),
            ],
          ),
        ),
        if (_users.length > 1)
          SizedBox(
            height: 56,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
              children: _users.map((user) {
                final username = user['username'] as String;
                final selected = username == _peer;
                final name = user['display_name']?.toString() ?? username;
                return Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    selected: selected,
                    avatar: CircleAvatar(
                      radius: 12,
                      child: Text(name.isEmpty ? '?' : name.characters.first.toUpperCase(),
                        style: const TextStyle(fontSize: 10)),
                    ),
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
                    final time = _displayTime(item['created_at']);
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
                                  Text(
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
                                      time,
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
                  tooltip: 'More options',
                  onPressed: null,
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
