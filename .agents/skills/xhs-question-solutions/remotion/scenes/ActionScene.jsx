import React from "react";
import {useCurrentFrame, useVideoConfig} from "remotion";
import {SceneShell} from "../components/SceneShell";
import {FieldCard, Reveal, SceneTitle} from "../components/Primitives";

export const ActionScene = ({scene, video}) => {
  const c = scene.content;
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const duration = Math.round((scene.end_ms - scene.start_ms) * fps / 1000);
  const fields = [
    {label: "适用", value: c.applies_when.join("；"), tone: "default"},
    {label: "怎么验证", value: c.verification, tone: "safe"},
    {label: "何时停止", value: c.stop_conditions.join("；"), tone: "warning"},
  ];
  const active = Math.min(fields.length - 1, Math.floor(frame / Math.max(1, duration) * fields.length));
  const field = fields[active];
  return <SceneShell scene={scene} video={video} eyebrow={`可执行动作 · 第 ${c.step_number} 步`}>
    <Reveal><SceneTitle>{c.text}</SceneTitle></Reveal>
    <div className="action-stage">
      <FieldCard key={field.label} label={field.label} tone={field.tone}>{field.value}</FieldCard>
      <div className="action-dots">{fields.map((item, index) => <span key={item.label} className={index === active ? "active" : ""}>{item.label}</span>)}</div>
      <div className="dynamic-field-probes" aria-hidden="true">{fields.map((item) => (
        <div className={`field-card field-${item.tone} dynamic-field-probe`} key={item.label}>
          <div className="field-card-label">{item.label}</div><div className="field-card-value">{item.value}</div>
        </div>
      ))}</div>
    </div>
  </SceneShell>;
};
