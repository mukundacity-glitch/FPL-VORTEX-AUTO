"""Exercise production notebook excerpts through narration, PCM and animation."""

import asyncio
import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from day3.transfer_desk import (
    patch_transfer_desk_animation_source,
    patch_transfer_desk_audio_source,
    patch_transfer_desk_narration_source,
)


class TransferDeskTimelineTests(unittest.TestCase):
    def namespace(self, root, hold_count):
        records = {}
        index = 0
        for action, count in (("BUY", 5), ("HOLD", hold_count), ("SELL", 5)):
            records[action] = []
            for _ in range(count):
                records[action].append(dict(index=index, player_id=index + 1,
                                            team_id=1, name=f"Player{index + 1}", action=action))
                index += 1

        def narrate(action, index):
            record = records[action][index]
            return f"Player{record['player_id']} from Club has quiet returns."

        async def synthesize(path, text, profile):
            words = [dict(text=word, start=index * .1, duration=.1)
                     for index, word in enumerate(text.split())]
            return dict(audio=str(path), words=words)

        def phrase_time(words, phrase, **kwargs):
            tokens = [word["text"].strip(".,:") for word in words]
            wanted = phrase.split()
            for index in range(len(tokens) - len(wanted) + 1):
                if tokens[index:index + len(wanted)] == wanted:
                    return words[index]["start"]
            raise RuntimeError(f"Missing player phrase: {phrase}")

        def fake_ffmpeg(args, **kwargs):
            target = Path(args[-1])
            if target.suffix == ".wav":
                with wave.open(str(target), "wb") as output:
                    output.setnchannels(1)
                    output.setsampwidth(2)
                    output.setframerate(48000)
                    output.writeframes(b"\x00\x00" * 48000)
            else:
                target.write_bytes(b"encoded-audio")

        namespace = dict(
            NEXT_GW=7, DAY3_TRANSFER_DESK=records, DAY3_TRANSFER_DESK_COUNT=index,
            Path=Path, subprocess=subprocess, re=re, AUDIO=root,
            AUDIO_WORK_DIR=root / "work", _d3_desk_narr=narrate,
            vx_narration_player=lambda pid: f"Player{pid}",
            vx_narration_team=lambda tid: "Club",
            vx_synthesize_audio=synthesize, vx_phrase_time=phrase_time,
            vx_full_word_narration=lambda text, **kwargs: text,
            vx_full_word_display=lambda text, **kwargs: text,
            vx_media_duration=lambda path: 1000,
            _d3_publish_audio=lambda pending, target: pending.replace(target),
        )
        fixture = json.loads((Path(__file__).parent / "fixtures/transfer_desk_contract.json").read_text())
        for name, patcher in (("narration", patch_transfer_desk_narration_source),
                              ("audio", patch_transfer_desk_audio_source),
                              ("animation", patch_transfer_desk_animation_source)):
            exec(compile(patcher(fixture[name]), name, "exec"), namespace)
        return namespace, fake_ffmpeg

    def test_zero_to_five_holds_survive_the_complete_card_timeline(self):
        for hold_count in range(6):
            with self.subTest(hold_count=hold_count), tempfile.TemporaryDirectory() as directory:
                ns, fake_ffmpeg = self.namespace(Path(directory), hold_count)
                script = ns["_d3_transfer_full_narration"]()
                self.assertIn("Now the holds", script)
                ns["check_spoken_cards"]()
                with patch.object(subprocess, "run", side_effect=fake_ffmpeg):
                    info = asyncio.run(ns["_d3_transfer_audio"]({}))
                cues = info["sync_cues"]
                self.assertEqual(len(cues["windows"]), 10 + hold_count)
                self.assertEqual(sum(not row["narrated"] for row in cues["windows"]),
                                 4 + max(0, hold_count - 3))
                ns["validate_animation"]("05_transfer_decision_desk", info)
                payload = {}
                ns["validate_payload"]("05_transfer_decision_desk", payload)
                self.assertEqual(len(payload["transfer_desk_windows"]), 10 + hold_count)
                expected_holds = min(3, hold_count)
                hold_section = ns["TRANSFER_DESK_NARRATION_PLAN"]["sections"][1]
                self.assertEqual(len(hold_section["players"]), expected_holds)
                self.assertEqual(len(hold_section["silent_cards"]), max(0, hold_count - 3))
                if hold_count == 0:
                    self.assertIn("No players meet", script)
                elif hold_count < 5:
                    self.assertIn(f"Only {hold_count} players meet", script)
                damaged = copy.deepcopy(info)
                damaged["sync_cues"]["windows"][0]["player_id"] = 9999
                with self.assertRaisesRegex(RuntimeError, "selection order"):
                    ns["validate_animation"]("05_transfer_decision_desk", damaged)
                damaged = copy.deepcopy(info)
                damaged["sync_cues"]["windows"][1]["start"] += .01
                with self.assertRaisesRegex(RuntimeError, "gap or overlap"):
                    ns["validate_animation"]("05_transfer_decision_desk", damaged)


if __name__ == "__main__":
    unittest.main()
