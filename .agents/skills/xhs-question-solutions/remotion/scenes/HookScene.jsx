import React from "react";
import {SceneShell} from "../components/SceneShell";
import {Lead, Reveal, SceneTitle} from "../components/Primitives";

export const HookScene = ({scene, video}) => (
  <SceneShell scene={scene} video={video} eyebrow="评论证据解法">
    <Reveal><div className="hook-kicker">先找根因，再谈处理</div></Reveal>
    <Reveal delay={8}><SceneTitle>{scene.content.social_title}</SceneTitle></Reveal>
    <Reveal delay={20}><Lead>{scene.content.summary}</Lead></Reveal>
    <Reveal delay={34}><div className="hook-route"><span>问题</span><i>→</i><span>证据</span><i>→</i><span>行动</span></div></Reveal>
  </SceneShell>
);
