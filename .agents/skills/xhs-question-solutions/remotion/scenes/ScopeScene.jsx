import React from "react";
import {SceneShell} from "../components/SceneShell";
import {FieldCard, Lead, Reveal, SceneTitle} from "../components/Primitives";

export const ScopeScene = ({scene, video}) => {
  const c = scene.content;
  return <SceneShell scene={scene} video={video} eyebrow="先看样本边界">
    <Reveal><SceneTitle>热度不等于真实</SceneTitle></Reveal>
    <Reveal delay={6}><Lead>先看采集了多少，再看评论说了什么。</Lead></Reveal>
    <div className="metric-grid">
      <FieldCard label="页面显示" delay={12}><strong>{video.meta.comments_total || "?"}</strong> 条</FieldCard>
      <FieldCard label="实际采集" delay={18}><strong>{video.meta.comments_collected}</strong> 条</FieldCard>
      <FieldCard label="完整性" tone={c.is_truncated ? "warning" : "safe"} delay={24}>{c.is_truncated ? "未采全" : "未标记截断"}</FieldCard>
    </div>
    <FieldCard label="来源与时间" delay={30}>{c.source_label} · {c.captured_at_label}</FieldCard>
  </SceneShell>;
};
