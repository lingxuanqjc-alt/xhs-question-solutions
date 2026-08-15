import React from "react";
import {interpolate, spring, useCurrentFrame, useVideoConfig} from "remotion";
import {SceneShell} from "../components/SceneShell";
import {Lead, SceneTitle} from "../components/Primitives";

const HOOK_SUMMARY_FRAME = (fps) => fps;
const HOOK_ROUTE_FRAME = (fps) => fps * 2;

export const HookScene = ({scene, video}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const summary = spring({frame: frame - HOOK_SUMMARY_FRAME(fps), fps, config: {damping: 22, stiffness: 130, mass: 0.6}});
  const route = spring({frame: frame - HOOK_ROUTE_FRAME(fps), fps, config: {damping: 22, stiffness: 145, mass: 0.55}});
  return <SceneShell scene={scene} video={video} eyebrow="评论证据解法">
    <div className="hook-question"><SceneTitle>{scene.content.social_title}</SceneTitle></div>
    <div className="hook-answer" style={{opacity: summary, translate: `0px ${interpolate(summary, [0, 1], [24, 0])}px`}}>
      <Lead>{scene.content.summary}</Lead>
    </div>
    <div className="hook-route" style={{opacity: route, translate: `${interpolate(route, [0, 1], [-30, 0])}px 0px`}}><span>问题</span><i>→</i><span>证据</span><i>→</i><span>行动</span></div>
  </SceneShell>;
};
