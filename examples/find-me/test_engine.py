import unittest
from engine import Duck,wrap

class Behavior(unittest.TestCase):
    def run_target(self,bearing):
        d=Duck(); d.wake()
        for _ in range(350):
            d.tick(.1,bearing)
            if d.state=='dialogue': return d
        self.fail(f'never aligned with {bearing}: {d}')
    def test_front_back_and_sides(self):
        for b in (0,40,-40,90,-90,179,-179):
            with self.subTest(b=b):
                d=self.run_target(b); self.assertLess(abs(wrap(b-d.body)),7);self.assertLess(abs(d.head),8)
    def test_rear_not_immediately_seen(self):
        d=Duck();d.wake();d.tick(.1,180);self.assertFalse(d.seen);self.assertEqual(d.state,'search')
    def test_timeout(self):
        d=Duck();d.wake()
        for _ in range(360): d.tick(.1)
        self.assertEqual(d.state,'idle')
    def test_multiple_people_stops_motion(self):
        d=Duck();d.wake();before=(d.body,d.head)
        for _ in range(20): d.tick(.1,30,True)
        self.assertEqual((d.body,d.head),before);self.assertEqual(d.state,'ambiguous')
    def test_lost_person_research(self):
        d=self.run_target(0)
        for _ in range(12):d.tick(.1)
        self.assertEqual(d.state,'search')
    def test_small_motion_doesnt_turn_body(self):
        d=self.run_target(0)
        for _ in range(50):d.tick(.1,10)
        self.assertEqual(d.body,0);self.assertEqual(d.state,'dialogue')
    def test_rate_and_head_limits(self):
        d=Duck();d.wake()
        for _ in range(340):
            body,head=d.body,d.head;d.tick(.1,-170)
            self.assertLessEqual(abs(wrap(d.body-body)),3.0001);self.assertLessEqual(abs(d.head),65)

if __name__=='__main__':unittest.main()
