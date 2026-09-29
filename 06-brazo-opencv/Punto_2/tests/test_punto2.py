"""Tests exclusivos de la comunicacion SPI/USB y vision del punto 2."""

import importlib.util
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from protocolo_spi_serial import (AckStream, SPI_COMMAND, SPI_POLL, checksum,
                                  decode_ack, decode_spi_ack, encode_digit, spi_frame)


class SerialSpiTests(unittest.TestCase):
    def test_pc_command_and_fragmented_ack(self):
        self.assertTrue(encode_digit(8, "7", "V").startswith(b"@DIG,8,7,V*"))
        payload = b"ACK,8,7,OK"
        ack = b"@" + payload + b"*" + ("%02X" % checksum(payload)).encode() + b"\n"
        stream = AckStream()
        self.assertEqual(stream.feed(b"inicio ESP32-A\n" + ack[:5]), [])
        self.assertEqual([(x.sequence, x.digit, x.status) for x in stream.feed(ack[5:])],
                         [(8, "7", "OK")])
        with self.assertRaises(ValueError):
            decode_ack(b"@ACK,8,7,OK*00\n")

    def test_spi_command_poll_and_matching_slave_answer(self):
        command = spi_frame(SPI_COMMAND, 513, "5", "V")
        poll = spi_frame(SPI_POLL, 513, "5", "V")
        self.assertEqual((len(command), len(poll)), (12, 12))
        self.assertEqual(command[1:5], bytes((2, 1, ord("5"), ord("V"))))
        self.assertEqual(command[5], checksum(command[:5]))
        reply = bytearray((0xAC, 2, 1, ord("5"), 0))
        reply.append(checksum(reply))
        reply.extend(bytes(6))
        self.assertEqual(decode_spi_ack(reply, 513, "5"), "OK")
        with self.assertRaises(ValueError):
            decode_spi_ack(reply, 514, "5")

    def test_arduino_sketch_matches_directory_name(self):
        folder = BASE / "esp32_esclava_oled_arduino"
        self.assertTrue((folder / (folder.name + ".ino")).is_file())


@unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow no esta instalado")
class TrainingOrientationTests(unittest.TestCase):
    def test_emnist_orientation_transposes_axes_without_changing_pixels(self):
        from PIL import Image
        from orientacion_emnist import upright
        sample = Image.new("L", (3, 2))
        sample.putdata((1, 2, 3, 4, 5, 6))
        corrected = upright(sample)
        self.assertEqual(corrected.size, (2, 3))
        self.assertEqual(tuple(corrected.tobytes()), (1, 4, 2, 5, 3, 6))


@unittest.skipUnless(importlib.util.find_spec("cv2") and importlib.util.find_spec("numpy"),
                     "OpenCV y NumPy no estan instalados")
class VisionTests(unittest.TestCase):
    def test_roi_contains_handwritten_like_digit(self):
        import cv2
        import numpy as np
        from vision import process_roi
        paper = np.full((220, 220, 3), 255, dtype=np.uint8)
        self.assertIsNone(process_roi(paper.copy()))
        cv2.putText(paper, "5", (45, 185), cv2.FONT_HERSHEY_SIMPLEX, 5,
                    (0, 0, 0), 12, cv2.LINE_AA)
        result = process_roi(paper)
        self.assertIsNotNone(result)
        image, box, _ = result
        self.assertEqual(image.shape, (28, 28))
        self.assertGreater(float(image.sum()), 15)
        self.assertGreater(box[2], 15)

    def test_horizontal_mirror_applies_to_frame_before_cnn_crop(self):
        import numpy as np
        from vision import orient_frame
        frame = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
        np.testing.assert_array_equal(orient_frame(frame, False), frame)
        np.testing.assert_array_equal(orient_frame(frame, True), frame[:, ::-1, :])

    def test_auto_detects_black_digit_inside_bright_phone_not_phone_border(self):
        import cv2
        import numpy as np
        from vision import process_roi
        scene = np.full((320, 320, 3), 30, dtype=np.uint8)
        cv2.rectangle(scene, (85, 25), (235, 293), (255, 255, 255), -1)
        cv2.putText(scene, "1", (110, 217), cv2.FONT_HERSHEY_SIMPLEX,
                    5, (0, 0, 0), 17, cv2.LINE_AA)
        answer = process_roi(scene)
        self.assertIsNotNone(answer)
        _, (x, y, w, h), preview = answer
        self.assertGreater(x, 90)
        self.assertGreater(y, 40)
        self.assertLess(w, 120)
        self.assertLess(h, 230)
        self.assertEqual(preview.shape, (28, 28))

    def test_blank_phone_does_not_become_digit(self):
        import cv2
        import numpy as np
        from vision import process_roi
        scene = np.full((320, 320, 3), 30, dtype=np.uint8)
        cv2.rectangle(scene, (85, 25), (235, 293), (255, 255, 255), -1)
        self.assertIsNone(process_roi(scene))

    def test_empty_graph_paper_does_not_become_digit(self):
        import cv2
        import numpy as np
        from vision import process_roi
        paper = np.full((280, 280, 3), 235, dtype=np.uint8)
        for coordinate in range(8, 280, 16):
            cv2.line(paper, (coordinate, 0), (coordinate, 279), (195,) * 3, 1)
            cv2.line(paper, (0, coordinate), (279, coordinate), (195,) * 3, 1)
        self.assertIsNone(process_roi(paper))

    def test_light_digit_on_dark_background_can_be_selected(self):
        import cv2
        import numpy as np
        from vision import process_roi
        scene = np.full((220, 220, 3), 15, dtype=np.uint8)
        cv2.putText(scene, "5", (45, 185), cv2.FONT_HERSHEY_SIMPLEX,
                    5, (245, 245, 245), 12, cv2.LINE_AA)
        self.assertIsNotNone(process_roi(scene, "light"))


if __name__ == "__main__":
    unittest.main()
