import React from "react";
import {AbsoluteFill, Sequence} from "remotion";
import {ActionScene} from "./scenes/ActionScene";
import {DisclosureScene, CtaScene} from "./scenes/DisclosureScenes";
import {EvidenceScene} from "./scenes/EvidenceScene";
import {HookScene} from "./scenes/HookScene";
import {ConflictScene, UnknownsScene} from "./scenes/RiskScenes";
import {ScopeScene} from "./scenes/ScopeScene";
import "./video.css";

const COMPONENTS = {
  hook: HookScene,
  scope: ScopeScene,
  action: ActionScene,
  evidence: EvidenceScene,
  conflict_risk: ConflictScene,
  risk_unknowns: UnknownsScene,
  disclosure: DisclosureScene,
  cta: CtaScene,
};

export const XhsQuestionVideo = ({video}) => (
  <AbsoluteFill className="video-root">
    {video.scenes.map((scene) => {
      const Component = COMPONENTS[scene.role];
      if (!Component) throw new Error(`UNSUPPORTED_VIDEO_ROLE(${scene.role})`);
      const from = Math.round(scene.start_ms * video.fps / 1000);
      const durationInFrames = Math.round((scene.end_ms - scene.start_ms) * video.fps / 1000);
      return <Sequence key={scene.scene_id} from={from} durationInFrames={durationInFrames} name={scene.scene_id}>
        <Component scene={scene} video={video} />
      </Sequence>;
    })}
  </AbsoluteFill>
);
