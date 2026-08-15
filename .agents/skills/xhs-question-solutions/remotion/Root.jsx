import React from "react";
import {Composition} from "remotion";
import {XhsQuestionVideo} from "./XhsQuestionVideo";

const fallbackVideo = {
  profile: "xhs-vertical-1080x1920-v1",
  width: 1080,
  height: 1920,
  fps: 30,
  duration_ms: 60000,
  duration_in_frames: 1800,
  unsafe_evidence_comment_ids: [],
  meta: {risk_level: "low", publish_status: "needs_review", audio: {kind: "none"}},
  scenes: [{scene_id: "preview:01", index: 1, role: "hook", start_ms: 0, end_ms: 60000,
    content: {social_title: "请传入视频 props", question: "请传入视频 props", summary: "运行 Python 构建器后，用生成的 .props.json 预览。"},
    narration: "请传入视频 props。", captions: [], evidence_comment_ids: [], persistent_notices: []}],
  appendix: {evidence: []},
};

export const RemotionRoot = () => (
  <Composition
    id="XhsQuestionVideo"
    component={XhsQuestionVideo}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={1800}
    defaultProps={{video: fallbackVideo}}
    calculateMetadata={({props}) => ({
      width: props.video.width,
      height: props.video.height,
      fps: props.video.fps,
      durationInFrames: props.video.duration_in_frames,
    })}
  />
);
