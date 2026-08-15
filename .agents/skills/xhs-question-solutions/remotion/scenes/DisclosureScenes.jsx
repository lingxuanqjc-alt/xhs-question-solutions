import React from "react";
import {SceneShell} from "../components/SceneShell";
import {FieldCard, Lead, Reveal, SceneTitle} from "../components/Primitives";

export const DisclosureScene = ({scene, video}) => (
  <SceneShell scene={scene} video={video} eyebrow="证据与披露">
    <Reveal><SceneTitle>可以参考，但不能跳过复核</SceneTitle></Reveal>
    <div className="disclosure-list">
      <FieldCard label="样本与来源" delay={8}>
        {scene.content.source_label} · {scene.content.is_truncated ? "评论未完整采集" : "按采集范围整理"}<br />
        {scene.content.coverage}
      </FieldCard>
      <FieldCard label="生成方式" delay={14}>AI 辅助整理 · 经验不等于事实</FieldCard>
      <FieldCard label="利益关系" delay={20}>未知，发布前人工确认</FieldCard>
      <FieldCard label="发布状态" tone={scene.content.publish_status === "ready" ? "safe" : "warning"} delay={26}>{scene.content.publish_status === "ready" ? "可发布" : "进入人工发布复核"}</FieldCard>
    </div>
  </SceneShell>
);

export const CtaScene = ({scene, video}) => (
  <SceneShell scene={scene} video={video} eyebrow="安全互动">
    <Reveal><div className="cta-mark">?</div></Reveal>
    <Reveal delay={8}><SceneTitle>{scene.content.question}</SceneTitle></Reveal>
    <Reveal delay={18}><Lead>{scene.content.stop_message}</Lead></Reveal>
    <Reveal delay={28}><div className="cta-footer">把能确认的现场信息补齐，再选择下一步。</div></Reveal>
  </SceneShell>
);
