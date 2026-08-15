import React from "react";
import {sceneFocusInterval, useFocusMotion} from "../components/FocusMotion";
import {SceneShell} from "../components/SceneShell";
import {FieldCard, Reveal, SceneTitle} from "../components/Primitives";

export const ActionScene = ({scene, video}) => {
  const c = scene.content;
  const fields = [
    {label: "适用", value: c.applies_when.join("；"), tone: "default"},
    {label: "怎么验证", value: c.verification, tone: "safe"},
    {label: "何时停止", value: c.stop_conditions.join("；"), tone: "warning"},
  ];
  const {active, styleFor, emphasisStyleFor} = useFocusMotion(fields.length, sceneFocusInterval(scene, fields.length));
  return <SceneShell scene={scene} video={video} eyebrow={`可执行动作 · 第 ${c.step_number} 步`}>
    <Reveal><SceneTitle>{c.text}</SceneTitle></Reveal>
    <div className="action-stage">
      <div className="focus-stack" data-focus-active={active}>{fields.map((item, index) => (
        <div className="focus-layer" style={styleFor(index)} key={item.label}>
          <FieldCard label={item.label} tone={item.tone}>{item.value}</FieldCard>
        </div>
      ))}</div>
      <div className="action-dots">{fields.map((item, index) => <span key={item.label} className={index === active ? "active" : ""} style={emphasisStyleFor(index)}>{item.label}</span>)}</div>
      <div className="dynamic-field-probes" aria-hidden="true">{fields.map((item) => (
        <div className={`field-card field-${item.tone} dynamic-field-probe`} key={item.label}>
          <div className="field-card-label">{item.label}</div><div className="field-card-value">{item.value}</div>
        </div>
      ))}</div>
    </div>
  </SceneShell>;
};
