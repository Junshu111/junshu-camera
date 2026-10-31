"""QR pixel contract; independent macOS Vision decoding is separate retained QA."""
import unittest

class QRTests(unittest.TestCase):
    def test_real_matrix_quiet_zone_and_integer_pixels(self):
        from camera_r4.share_ui import qr_image
        url='http://192.168.1.12:49152/s/0123456789abcdefghijklmnopqrstuv'
        a=qr_image(url);b=qr_image(url)
        self.assertEqual(a,b)
        self.assertGreaterEqual(a.width(),250)
        self.assertLessEqual(a.width(),360)
        self.assertEqual(a.width(),a.height())
        # Four complete white modules surround the code, rendered without blur.
        for x,y in [(0,0),(a.width()-1,a.height()-1),(a.width()//2,0),(0,a.height()//2)]:self.assertEqual(a.pixelColor(x,y).name(),'#ffffff')
        colors={a.pixelColor(x,y).name() for y in range(a.height()) for x in range(a.width())}
        self.assertEqual(colors,{'#000000','#ffffff'})

if __name__=='__main__':unittest.main()
