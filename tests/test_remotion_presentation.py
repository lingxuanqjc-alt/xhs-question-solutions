import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # Optional visual regression dependency.
    Image = None


ROOT = Path(__file__).resolve().parents[1]
REMOTION = ROOT / ".agents/skills/xhs-question-solutions/remotion"
SKILL = REMOTION.parent


def source(relative):
    return (REMOTION / relative).read_text(encoding="utf-8")


class RemotionPresentationIntentTests(unittest.TestCase):
    def test_hook_prioritizes_the_specific_problem_then_reveals_value(self):
        hook = source("scenes/HookScene.jsx")
        self.assertNotIn("先找根因，再谈处理", hook)
        self.assertIn("useCurrentFrame", hook)
        self.assertIn("HOOK_SUMMARY_FRAME", hook)
        self.assertIn("HOOK_ROUTE_FRAME", hook)
        self.assertLess(hook.index("scene.content.social_title"), hook.index("scene.content.summary"))
        self.assertLess(hook.index("scene.content.summary"), hook.index("hook-route"))

    def test_caption_motion_is_frame_driven_and_keeps_safety_immediate(self):
        overlay = source("components/CaptionOverlay.jsx")
        shell = source("components/SceneShell.jsx")
        css = source("video.css")
        for token in ("useCurrentFrame", "useVideoConfig", "spring", "interpolate"):
            self.assertIn(token, overlay)
        self.assertIn("未核验高风险观点，不是操作建议", overlay)
        self.assertIn("opacity: safety ? 1 : opacity", overlay)
        self.assertIn("caption-safety", overlay)
        self.assertIn("sceneStartMs={scene.start_ms}", shell)
        self.assertNotRegex(css, r"\b(?:animation|transition)(?:-[\w-]+)?\s*:")

    def test_dense_scenes_use_a_shared_sub_2_5_second_focus_rhythm(self):
        focus = source("components/FocusMotion.jsx")
        self.assertIn("FOCUS_INTERVAL_SECONDS = 2.4", focus)
        self.assertIn("spring", focus)
        self.assertIn("emphasisStyleFor", focus)
        for relative in (
            "scenes/ActionScene.jsx",
            "scenes/EvidenceScene.jsx",
            "scenes/RiskScenes.jsx",
            "scenes/DisclosureScenes.jsx",
        ):
            with self.subTest(scene=relative):
                scene = source(relative)
                self.assertIn("useFocusMotion", scene)
                self.assertIn("sceneFocusInterval", scene)
                self.assertNotIn("Math.floor(frame / Math.max(1, duration)", scene)

    @unittest.skipUnless(Image and shutil.which("node") and (SKILL / "node_modules/@remotion/renderer").exists(), "Remotion still runtime is optional")
    def test_focus_boundary_still_does_not_show_two_copies_of_the_card(self):
        props = next((ROOT / "examples/sample-video").glob("*.props.json"))
        renderer = SKILL / "scripts/render_video.mjs"
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["node", str(renderer), "--props", str(props), "--output", tmp, "--still-frames", "385,390"],
                cwd=SKILL, text=True, encoding="utf-8", capture_output=True, check=False,
            )
            if result.returncode and "browser" in (result.stderr + result.stdout).lower():
                self.skipTest("A local Chromium browser is optional")
            self.assertEqual(0, result.returncode, result.stderr + result.stdout)

            def pale_ink(frame):
                image = Image.open(Path(tmp) / f"frame-{frame:04d}.png").convert("RGB").crop((80, 590, 900, 915))
                pixels = image.load()
                return sum(1 for y in range(image.height) for x in range(image.width) if sum(pixels[x, y]) / 3 < 225)

            # A ghosted outgoing card adds a second field of pale glyphs at frame 385.
            self.assertLessEqual(pale_ink(385), pale_ink(390) * 1.12)

    def test_safety_and_disclosure_surfaces_remain_in_the_shared_shell(self):
        shell = source("components/SceneShell.jsx")
        for invariant in (
            "persistent-warning",
            "evidence-strip",
            "caption-probe",
            "dynamic-field-probe",
            "无配音版 · 静音也能看懂",
        ):
            self.assertIn(invariant, shell)

    def test_scene_cuts_keep_content_visible_while_the_entry_settles(self):
        shell = source("components/SceneShell.jsx")
        primitives = source("components/Primitives.jsx")
        self.assertIn("SCENE_EXIT_OPACITY_FLOOR", shell)
        self.assertIn("[1, SCENE_EXIT_OPACITY_FLOOR]", shell)
        self.assertNotIn("opacity: enter", shell)
        self.assertIn("REVEAL_OPACITY_FLOOR", primitives)
        self.assertIn("[REVEAL_OPACITY_FLOOR, 1]", primitives)

    @unittest.skipUnless(Image and shutil.which("node") and (SKILL / "node_modules/@remotion/renderer").exists(), "Remotion still runtime is optional")
    def test_real_scene_cut_stills_keep_subject_ink_and_the_safety_banner(self):
        props = next((ROOT / "examples/sample-video").glob("*.props.json"))
        renderer = SKILL / "scripts/render_video.mjs"
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["node", str(renderer), "--props", str(props), "--output", tmp, "--still-frames", "89,90,239,240,246"],
                cwd=SKILL, text=True, encoding="utf-8", capture_output=True, check=False,
            )
            if result.returncode and "browser" in (result.stderr + result.stdout).lower():
                self.skipTest("A local Chromium browser is optional")
            self.assertEqual(0, result.returncode, result.stderr + result.stdout)

            def load(frame):
                return Image.open(Path(tmp) / f"frame-{frame:04d}.png").convert("RGB")

            def subject_ink(frame):
                crop = load(frame).crop((80, 260, 900, 1000))
                pixels = crop.load()
                return sum(1 for y in range(crop.height) for x in range(crop.width) if sum(pixels[x, y]) / 3 < 180)

            def subject_contrast(frame):
                crop = load(frame).crop((80, 300, 900, 1000))
                luminances = []
                pixels = crop.load()
                for y in range(crop.height):
                    for x in range(crop.width):
                        channels = []
                        for channel in pixels[x, y]:
                            normalized = channel / 255
                            channels.append(normalized / 12.92 if normalized <= 0.04045 else ((normalized + 0.055) / 1.055) ** 2.4)
                        luminances.append(0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2])
                luminances.sort()
                dark = luminances[int(len(luminances) * 0.05)]
                light = luminances[int(len(luminances) * 0.90)]
                return (light + 0.05) / (dark + 0.05)

            for frame in (89, 90, 239, 240):
                with self.subTest(frame=frame):
                    self.assertGreater(subject_ink(frame), 50_000)
            for frame in (90, 240):
                with self.subTest(readable_entry_frame=frame):
                    self.assertGreaterEqual(subject_contrast(frame), 3.0)
            self.assertEqual(load(240).getpixel((400, 190)), load(246).getpixel((400, 190)))


if __name__ == "__main__":
    unittest.main()
