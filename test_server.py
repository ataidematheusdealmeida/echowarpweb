import threading
import os
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import server


class PersistentSessionTests(unittest.TestCase):
    def test_restart_logout_expiry_and_credential_change(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, "SESSIONS_FILE", Path(folder) / "sessions.sqlite3"):
            httpd = server.EchoWarpHTTPServer(("127.0.0.1", 0), server.EchoWarpHandler, None)
            try:
                token = httpd.create_session(3600)
                expired = httpd.create_session(-1)
                self.assertTrue(httpd.valid_session(token))
                self.assertFalse(httpd.valid_session(expired))
                self.assertFalse(httpd.valid_session("invented-token"))
                self.assertNotIn(token.encode(), server.SESSIONS_FILE.read_bytes())
            finally:
                httpd.server_close()
            restarted = server.EchoWarpHTTPServer(("127.0.0.1", 0), server.EchoWarpHandler, None)
            try:
                self.assertTrue(restarted.valid_session(token))
                with patch.dict(os.environ, {"WEB_AUTH_PASSWORD": "changed-test-password"}):
                    self.assertFalse(restarted.valid_session(token))
                restarted.delete_session(token)
                self.assertFalse(restarted.valid_session(token))
            finally:
                restarted.server_close()


class ReconnectTests(unittest.TestCase):
    def controller(self):
        with patch.object(server.EchoWarpController, "load_config", return_value=server.default_config()), patch.object(server.EchoWarpController, "load_servers", return_value=[]):
            controller = server.EchoWarpController()
        controller.write_status = Mock()
        controller.save_config = Mock()
        controller.stop_process = Mock()
        controller.echowarp_proc = Mock()
        controller.echowarp_proc.wait.return_value = 1
        return controller

    def test_pending_retry_after_stop_or_server_switch(self):
        for action in ("stop", "switch", "retry", "shutdown"):
            with self.subTest(action=action), patch.object(server, "append_log"):
                controller = self.controller()
                controller.start_echowarp = Mock()
                proc = controller.echowarp_proc
                entered = threading.Event()
                release = threading.Event()

                def wait(_delay):
                    entered.set()
                    self.assertTrue(release.wait(2))
                    return action == "shutdown"

                controller.stop_event = Mock()
                controller.stop_event.is_set.return_value = False
                controller.stop_event.wait.side_effect = wait
                worker = threading.Thread(target=controller.monitor_echowarp, args=(proc,))
                worker.start()
                try:
                    self.assertTrue(entered.wait(2))
                    if action == "stop":
                        controller.stop_echowarp()
                    elif action == "switch":
                        controller.restart_echowarp()
                finally:
                    release.set()
                    worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(controller.start_echowarp.call_count, int(action in ("switch", "retry")))

    def test_start_does_not_duplicate_running_process(self):
        controller = self.controller()
        controller.echowarp_proc.poll.return_value = None
        with patch.object(server.subprocess, "Popen") as spawn:
            controller.start_echowarp()
        spawn.assert_not_called()

    def test_old_process_logs_cannot_change_connection(self):
        controller = self.controller()
        old = Mock()
        old.stdout = iter(["invalid password\n"])
        with patch.object(server, "append_log"), patch("builtins.print"):
            controller.pipe_logs("echowarp", old)
        self.assertFalse(controller.connection_blocked)
        controller.write_status.assert_not_called()

    def test_retention_preserves_recent_and_active_segment(self):
        controller = self.controller()
        controller.config["recording_retention_hours"] = 1
        controller.recording_proc = Mock()
        controller.recording_proc.poll.return_value = None
        controller.recording_prefix = "20261007-120000-abcdef12"
        with tempfile.TemporaryDirectory() as folder, patch.object(server, "RECORDINGS_DIR", Path(folder)):
            expired = Path(folder) / "20261006-120000-abcdef12-00000.m4a"
            recent = Path(folder) / "20261007-110000-abcdef12-00000.m4a"
            finished = Path(folder) / (controller.recording_prefix + "-00000.m4a")
            active = Path(folder) / (controller.recording_prefix + "-00001.m4a")
            for path in (expired, recent, finished, active):
                path.write_bytes(b"audio")
            for path in (expired, finished, active):
                os.utime(path, (time.time() - 7200,) * 2)
            controller.cleanup_recordings()
            self.assertFalse(expired.exists())
            self.assertFalse(finished.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(active.exists())
            self.assertEqual([item["name"] for item in controller.recording_files()], [recent.name])
            controller.recording_proc = None
            controller.cleanup_recordings()
            self.assertFalse(active.exists())

    def test_recording_requires_connection_and_avoids_duplicates(self):
        controller = self.controller()
        with self.assertRaises(ValueError):
            controller.start_recording()
        controller.status["state"] = "connected"
        with tempfile.TemporaryDirectory() as folder, patch.object(server, "RECORDINGS_DIR", Path(folder)), patch.object(server.subprocess, "Popen") as spawn, patch.object(server.threading, "Thread"):
            spawn.return_value.poll.return_value = None
            controller.start_recording()
            controller.start_recording()
            spawn.assert_called_once()
            args = spawn.call_args.args[0]
            self.assertIn("segment", args)
            self.assertEqual(args[args.index("-segment_time") + 1], "1800")
            self.assertTrue(args[-1].endswith("-%05d.m4a"))
            controller.stop_echowarp()
            spawn.return_value.send_signal.assert_called_once_with(server.signal.SIGINT)
            self.assertFalse(controller.config["enabled"])
            self.assertIsNone(controller.recording_proc)

    def test_retention_validation(self):
        for value in (0, -1, float("nan"), float("inf"), 87601):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server.merge_config(server.default_config(), {"recording_retention_hours": value})
        self.assertEqual(server.merge_config(server.default_config(), {"recording_retention_hours": 24})["recording_retention_hours"], 24)

    def test_history_selection(self):
        playlist = '#EXTM3U\n#EXTINF:0.5,\nsegment_00001.ts\n#EXTINF:0.5,\nsegment_00002.ts\n#EXTINF:0.5,\nsegment_00003.ts\n'
        selected, total = server.history_segments(playlist, 0.75)
        self.assertEqual([name for name, _ in selected], ['segment_00002.ts', 'segment_00003.ts'])
        self.assertEqual(total, 1)
        self.assertEqual(server.history_segments(playlist, 1800)[1], 1.5)
        with self.assertRaises(ValueError):
            server.history_segments('#EXTINF:1,\n../secret.ts\n', 60)

    def test_status_probe_never_opens_signaling_port(self):
        response = Mock()
        response.read.return_value = b'{"server_version":"0.9.2"}'
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch.object(server, "build_opener", return_value=opener):
            self.assertTrue(server.check_server_online("100.94.39.35", 4415))
        opener.open.assert_called_once_with("http://100.94.39.35:4416/api/v1/session/info", timeout=1)

    def test_key_exchange_failure_is_not_wrong_password(self):
        controller = self.controller()
        controller.block_echowarp = Mock()
        controller.observe_echowarp_log('server authentication failed: expected key_exchange, got auth_result: [E305] key exchange failed')
        self.assertEqual(controller.block_echowarp.call_args.args[0], "handshake_error")
        controller.observe_echowarp_log('authentication failed: IP banned')
        self.assertEqual(controller.block_echowarp.call_args.args[0], "access_blocked")
        controller.observe_echowarp_log('authentication failed: Invalid password')
        self.assertEqual(controller.block_echowarp.call_args.args[0], "auth_error")


if __name__ == "__main__":
    unittest.main()
