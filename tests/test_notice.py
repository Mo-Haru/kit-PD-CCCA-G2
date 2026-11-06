import base64
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TEST_ENV = dict(
    LINE_CHANNEL_ACCESS_TOKEN='test-token',
    LINE_CHANNEL_SECRET='test-secret',
    LINE_TARGET_USER_ID='U' + '1' * 32,
    DAILY_NOTIFICATION_TIME='18:00',
    ENABLE_SCHEDULE='true',
)
spec = importlib.util.spec_from_file_location('private_note', ROOT / 'notice/notice.py')
bot = importlib.util.module_from_spec(spec)
with patch.dict(os.environ, TEST_ENV):
    spec.loader.exec_module(bot)


class BotTests(unittest.TestCase):
    def setUp(self):
        self.client = bot.app.test_client()

    def event(self, kind, **fields):
        if kind == 'message':
            fields['message']['quoteToken'] = 'test-quote-token'
        return dict(type=kind, timestamp=0, mode='active',
                    source={'type': 'user', 'userId': 'U' + '1' * 32},
                    replyToken='reply-token', webhookEventId='test-event',
                    deliveryContext={'isRedelivery': False}, **fields)

    def post(self, event=None, raw=None, signature=None):
        body = raw if raw is not None else json.dumps(
            {'destination': 'U' + '2' * 32, 'events': [] if event is None else [event]},
            ensure_ascii=False)
        signature = signature or base64.b64encode(hmac.new(
            b'test-secret', body.encode(), hashlib.sha256).digest()).decode()
        return self.client.post('/callback', data=body.encode(),
                                headers={'X-Line-Signature': signature},
                                content_type='application/json')

    @patch.object(bot.MessagingApi, 'push_message_with_http_info')
    def test_push_recipient_and_buttons(self, send):
        bot.send_question()
        req = send.call_args.args[0].to_dict()
        self.assertEqual(req['to'], 'U' + '1' * 32)
        buttons = req['messages'][0]['quickReply']['items']
        self.assertEqual([b['action']['data'] for b in buttons],
                         ['answer:q1:hobby', 'answer:q1:family', 'answer:q1:undecided'])
        self.assertTrue(all(b['action']['type'] == 'postback' for b in buttons))

    @patch.object(bot.MessagingApi, 'reply_message_with_http_info')
    def test_text_echo(self, send):
        res = self.post(self.event('message', message={'type': 'text', 'id': '1', 'text': '接続テスト'}))
        self.assertEqual(res.status_code, 200)
        req = send.call_args.args[0]
        self.assertEqual(req.reply_token, 'reply-token')
        self.assertEqual(req.messages[0].text, '接続テスト')

    @patch.object(bot.MessagingApi, 'reply_message_with_http_info')
    def test_question_command(self, send):
        res = self.post(self.event('message', message={'type': 'text', 'id': '1', 'text': '質問'}))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(send.call_args.args[0].messages[0].quick_reply.items), 3)

    @patch.object(bot.MessagingApi, 'reply_message_with_http_info')
    def test_all_choices(self, send):
        for key, (_, expected) in bot.CHOICES.items():
            with self.subTest(key=key):
                res = self.post(self.event('postback', postback={'data': f'answer:q1:{key}'}))
                self.assertEqual(res.status_code, 200)
                self.assertIn(expected, send.call_args.args[0].messages[0].text)

    @patch.object(bot.MessagingApi, 'reply_message_with_http_info')
    def test_unknown_choice(self, send):
        res = self.post(self.event('postback', postback={'data': 'answer:q1:invalid'}))
        self.assertEqual(res.status_code, 200)
        self.assertIn('もう一度', send.call_args.args[0].messages[0].text)

    @patch.object(bot.MessagingApi, 'reply_message_with_http_info')
    def test_invalid_signature_no_reply(self, send):
        self.assertEqual(self.post(signature='invalid').status_code, 400)
        send.assert_not_called()

    def test_missing_signature(self):
        self.assertEqual(self.client.post('/callback', data='{}').status_code, 400)

    def test_signed_invalid_json(self):
        self.assertEqual(self.post(raw='{').status_code, 400)

    def test_verification_empty_events(self):
        self.assertEqual(self.post().status_code, 200)

    @patch.object(bot.MessagingApi, 'reply_message_with_http_info',
                  side_effect=bot.ApiException(status=429))
    def test_api_failure(self, send):
        res = self.post(self.event('message', message={'type': 'text', 'id': '1', 'text': 'テスト'}))
        self.assertEqual(res.status_code, 502)

    def test_notification_time_in_japan(self):
        job = bot.build_scheduler().get_job('daily_question')
        before = datetime(2026, 11, 6, 17, 59, tzinfo=bot.JST)
        after = datetime(2026, 11, 6, 18, 1, tzinfo=bot.JST)
        self.assertEqual(job.trigger.get_next_fire_time(None, before),
                         datetime(2026, 11, 6, 18, 0, tzinfo=bot.JST))
        self.assertEqual(job.trigger.get_next_fire_time(None, after),
                         datetime(2026, 11, 7, 18, 0, tzinfo=bot.JST))
        self.assertEqual(job.max_instances, 1)

    def test_invalid_settings(self):
        with patch.object(bot, 'DAILY_TIME', '25:61'), self.assertRaises(ValueError):
            bot.build_scheduler()
        with patch.object(bot, 'TARGET_USER_ID', ''), self.assertRaises(ValueError):
            bot.send_question()

    @patch.object(bot, 'send_question')
    @patch.object(bot, 'build_scheduler')
    def test_manual_send_does_not_start_scheduler(self, scheduler, send):
        with patch.object(sys, 'argv', ['app.py', '--send-now']):
            bot.main()
        send.assert_called_once()
        scheduler.assert_not_called()

    @patch.object(bot.app, 'run')
    @patch.object(bot, 'build_scheduler')
    def test_server_starts_and_stops_scheduler(self, build, run):
        with patch.object(sys, 'argv', ['app.py']):
            bot.main()
        build.return_value.start.assert_called_once()
        build.return_value.shutdown.assert_called_once()
        self.assertFalse(run.call_args.kwargs['use_reloader'])

    @patch.object(bot.app, 'run')
    @patch.object(bot, 'build_scheduler')
    def test_disabled_schedule(self, build, run):
        with patch.object(bot, 'SCHEDULE_SETTING', 'false'), patch.object(sys, 'argv', ['app.py']):
            bot.main()
        build.assert_not_called()
        run.assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
