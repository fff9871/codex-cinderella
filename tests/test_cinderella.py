import unittest
from cinderella import check


class WatcherTests(unittest.TestCase):
    def card(self, cid='a', expiry=4600, **extra):
        return dict(id=cid, expiresAt=expiry, status='available', resetType='codexRateLimits', **extra)

    def fake(self, cards, outcome='reset'):
        calls = []
        def request(method, params):
            calls.append((method, params))
            if method.endswith('/consume'):
                return {'outcome': outcome}
            return {'rateLimitResetCredits': {'availableCount': 2, 'credits': cards}}
        return request, calls

    def test_boundary_and_earliest_only(self):
        request, calls = self.fake([self.card('b', 4500), self.card('a', 4600)])
        state, writes = {}, []
        check(state, lambda s: writes.append(str(s)), request, now=1000)
        consume = [p for m, p in calls if m.endswith('/consume')]
        self.assertEqual(len(consume), 1)
        self.assertEqual(consume[0]['creditId'], 'b')
        self.assertTrue(state['b']['done'])
        self.assertEqual(len(writes), 2)
        self.assertEqual(calls[-1][0], 'account/rateLimits/read')

    def test_skip_future_expired_no_expiry_and_dry_run(self):
        cards = [self.card('future', 4601), self.card('expired', 1000), self.card('forever', None)]
        request, calls = self.fake(cards)
        check({}, lambda _: None, request, now=1000)
        self.assertEqual(len(calls), 1)
        request, calls = self.fake([self.card()])
        check({}, lambda _: self.fail('dry run persisted'), request, now=1000, dry_run=True)
        self.assertEqual(len(calls), 1)

    def test_missing_details(self):
        request, _ = self.fake(None)
        with self.assertRaises(RuntimeError):
            check({}, lambda _: None, request, now=1000)

    def test_timeout_keeps_persisted_idempotency_key(self):
        state, persisted, keys = {}, [], []
        base, _ = self.fake([self.card()])
        def request(method, params):
            if method.endswith('/consume'):
                self.assertTrue(persisted)
                keys.append(params['idempotencyKey'])
                raise TimeoutError()
            return base(method, params)
        for _ in range(2):
            with self.assertRaises(TimeoutError):
                check(state, lambda s: persisted.append(str(s)), request, now=1000)
        self.assertEqual(keys[0], keys[1])

    def test_nothing_to_reset_allows_fresh_attempt(self):
        state = {}
        request, _ = self.fake([self.card()], 'nothingToReset')
        check(state, lambda _: None, request, now=1000)
        key = state['a']['key']
        check(state, lambda _: None, request, now=1000)
        self.assertNotEqual(key, state['a']['key'])
        self.assertNotIn('done', state['a'])

    def test_already_redeemed_not_consumed_again(self):
        state = {}
        request, calls = self.fake([self.card()], 'alreadyRedeemed')
        check(state, lambda _: None, request, now=1000)
        check(state, lambda _: None, request, now=1000)
        self.assertEqual(sum(m.endswith('/consume') for m, _ in calls), 1)


if __name__ == '__main__':
    unittest.main()
