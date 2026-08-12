import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

export type CreatorScene = {
  id: string;
  start_second: number;
  end_second: number;
  purpose: string;
  voiceover: string;
  on_screen_text: string;
  claim_ids: string[];
};

export type CreatorVideoProps = {
  topic: string;
  sourceLabel: string;
  scenes: CreatorScene[];
};

const palette = ['#5b5cf0', '#2563eb', '#0891b2', '#7c3aed', '#db2777'];

export const CreatorVideo: React.FC<CreatorVideoProps> = ({topic, sourceLabel, scenes}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const second = frame / fps;
  const sceneIndex = Math.min(
    scenes.length - 1,
    Math.max(0, scenes.findIndex((scene) => second >= scene.start_second && second < scene.end_second)),
  );
  const scene = scenes[sceneIndex] ?? scenes[0];
  const localFrame = frame - scene.start_second * fps;
  const opacity = interpolate(localFrame, [0, 12, 150, 180], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const translateY = interpolate(localFrame, [0, 20], [40, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const accent = palette[sceneIndex % palette.length];
  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(circle at 80% 15%, ${accent}, #10162f 55%)`,
        color: '#f8fafc',
        fontFamily: 'Arial, sans-serif',
        padding: 78,
      }}
    >
      <div style={{fontSize: 28, fontWeight: 800, letterSpacing: 3, opacity: 0.8}}>
        SOLOSCALE · CREATOR VIDEO
      </div>
      <div style={{marginTop: 90, fontSize: 54, lineHeight: 1.12, fontWeight: 800}}>{topic}</div>
      <div
        style={{
          alignSelf: 'center',
          background: 'rgba(255,255,255,.12)',
          border: `2px solid ${accent}`,
          borderRadius: 36,
          marginTop: 140,
          padding: 44,
          opacity,
          transform: `translateY(${translateY}px)`,
        }}
      >
        <div style={{color: '#c7d2fe', fontSize: 26, fontWeight: 800}}>{scene.purpose}</div>
        <div style={{fontSize: 60, fontWeight: 800, lineHeight: 1.15, marginTop: 30}}>
          {scene.voiceover}
        </div>
        <div style={{fontSize: 31, lineHeight: 1.4, marginTop: 48, opacity: 0.88}}>
          {scene.on_screen_text}
        </div>
      </div>
      <div style={{marginTop: 'auto', fontSize: 24, opacity: 0.7}}>
        {scene.claim_ids.join(' · ') || 'CTA'} · {sourceLabel}
      </div>
    </AbsoluteFill>
  );
};
