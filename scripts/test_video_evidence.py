"""Local regression checks for timed evidence sampling, without external services."""
import unittest
from prepare_video_evidence import frame_times


class FrameSamplingTests(unittest.TestCase):
    def test_short_clip_times_are_unique_ordered_and_inside_media(self):
        for duration in [0.05,0.5,1,9.5,137]:
            with self.subTest(duration=duration):
                times=frame_times(duration,3)
                self.assertEqual(times,sorted(set(times)))
                self.assertEqual(times[0],0)
                self.assertTrue(all(0<=t<duration for t in times))

    def test_first_three_seconds_are_explicit_samples(self):
        self.assertTrue({0.,1.,2.,3.}.issubset(frame_times(9.5,3)))
        self.assertIn(9.4,frame_times(9.5,3))

    def test_dense_sampler_keeps_subsecond_times(self):
        times=frame_times(2,.5)
        self.assertTrue({0,.5,1,1.5,1.9}.issubset(times))

    def test_invalid_and_unbounded_requests_rejected(self):
        for duration,interval in [(0,3),(2,0),(-1,3),(2,-1),(float('nan'),3),(2,float('inf')),(1000,.1)]:
            with self.subTest(duration=duration,interval=interval):
                with self.assertRaises(ValueError):frame_times(duration,interval)


if __name__=='__main__':unittest.main()
