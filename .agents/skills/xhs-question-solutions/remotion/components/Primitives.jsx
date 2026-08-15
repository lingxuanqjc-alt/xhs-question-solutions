import React from "react";
import {interpolate, spring, useCurrentFrame, useVideoConfig} from "remotion";

export const Reveal = ({delay = 0, children, className = ""}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const value = spring({frame: frame - delay, fps, config: {damping: 20, stiffness: 110, mass: 0.65}});
  const opacity = interpolate(value, [0, 1], [0, 1]);
  return <div className={className} style={{opacity, transform: `translateY(${(1 - value) * 28}px)`}}>{children}</div>;
};

export const SceneTitle = ({children}) => <h1 className="scene-title">{children}</h1>;
export const Lead = ({children}) => <div className="scene-lead">{children}</div>;

export const FieldCard = ({label, children, tone = "default", delay = 0}) => (
  <Reveal delay={delay} className={`field-card field-${tone}`}>
    <div className="field-card-label">{label}</div>
    <div className="field-card-value">{children}</div>
  </Reveal>
);

export const EvidenceQuote = ({label, id, children, tone = "default", delay = 0}) => (
  <Reveal delay={delay} className={`quote-card quote-${tone}`}>
    <div className="quote-label">{label}<b>#{id}</b></div>
    <div className="quote-text">{children}</div>
  </Reveal>
);
