import React from "react";
import {SceneShell} from "../components/SceneShell";
import {EvidenceQuote, Lead, Reveal, SceneTitle} from "../components/Primitives";

export const EvidenceScene = ({scene, video}) => {
  const {experience, counterexample, boundary} = scene.content;
  return <SceneShell scene={scene} video={video} eyebrow="正反样本一起看">
    <Reveal><SceneTitle>个案提示方向，不替你下结论</SceneTitle></Reveal>
    <div className="evidence-grid">
      {experience ? <EvidenceQuote label="一个亲历个案" id={experience.comment_id} tone="safe" delay={10}>{experience.claim}</EvidenceQuote> : <EvidenceQuote label="亲历个案" id="—" delay={10}>当前样本没有可用亲历个案</EvidenceQuote>}
      {counterexample ? <EvidenceQuote label="一个失败反例" id={counterexample.comment_id} tone="warning" delay={18}>{counterexample.claim}</EvidenceQuote> : <EvidenceQuote label="失败反例" id="—" delay={18}>当前样本没有明确失败反例</EvidenceQuote>}
    </div>
    <Reveal delay={28}><Lead>{boundary}</Lead></Reveal>
  </SceneShell>;
};
