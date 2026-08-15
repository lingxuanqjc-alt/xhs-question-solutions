import React from "react";
import {useCurrentFrame, useVideoConfig} from "remotion";
import {SceneShell} from "../components/SceneShell";
import {EvidenceQuote, FieldCard, Lead, Reveal, SceneTitle} from "../components/Primitives";

const unsafeIds = (video) => new Set(video.appendix.evidence.filter((item) => item.safety_warning).map((item) => item.comment_id));

export const ConflictScene = ({scene, video}) => {
  const dangerous = unsafeIds(video);
  const positions = scene.content.conflicts.flatMap((conflict) => conflict.positions.map((position) => ({topic: conflict.topic, ...position})));
  return <SceneShell scene={scene} video={video} eyebrow="冲突要表面化">
    <Reveal><SceneTitle>评论有分歧，不能按点赞裁决</SceneTitle></Reveal>
    <div className="conflict-stack">
      {positions.map((position, index) => {
        const unsafe = position.evidence_comment_ids.some((id) => dangerous.has(id));
        return <EvidenceQuote key={`${position.topic}-${index}`} label={unsafe ? "未核验 · 勿照做" : index === 0 ? "观点 A" : "观点 B"}
          id={position.evidence_comment_ids.join(" · ")} tone={unsafe ? "danger" : "warning"} delay={10 + index * 8}>{position.claim}</EvidenceQuote>;
      })}
      {!positions.length ? <Lead>当前样本不足以形成明确冲突。</Lead> : null}
    </div>
  </SceneShell>;
};

export const UnknownsScene = ({scene, video}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const duration = Math.round((scene.end_ms - scene.start_ms) * fps / 1000);
  const fields = [
    ...scene.content.unknowns.map((value, index) => ({label: `待确认 ${index + 1}`, value, tone: "default"})),
    {label: "停止边界", value: scene.content.stop_conditions.join("；"), tone: "warning"},
  ];
  const active = Math.min(fields.length - 1, Math.floor(frame / Math.max(1, duration) * fields.length));
  const field = fields[active];
  return <SceneShell scene={scene} video={video} eyebrow="发布前还缺什么">
    <Reveal><SceneTitle>这些未知项，会改变处理方案</SceneTitle></Reveal>
    <div className="action-stage">
      <FieldCard key={field.label} label={field.label} tone={field.tone}>{field.value}</FieldCard>
      <div className="action-dots">{fields.map((item, index) => <span key={item.label} className={index === active ? "active" : ""}>{index + 1}</span>)}</div>
      <div className="dynamic-field-probes" aria-hidden="true">{fields.map((item) => (
        <div className={`field-card field-${item.tone} dynamic-field-probe`} key={item.label}>
          <div className="field-card-label">{item.label}</div><div className="field-card-value">{item.value}</div>
        </div>
      ))}</div>
    </div>
  </SceneShell>;
};
