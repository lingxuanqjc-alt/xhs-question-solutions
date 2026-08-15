import React from "react";

export const CaptionOverlay = ({caption, sceneId}) => {
  return <div className={`caption-card ${caption ? "caption-visible" : ""}`} data-caption-scene={sceneId}>{caption?.text || ""}</div>;
};
