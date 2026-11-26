import React from "react";
import { Composition } from "remotion";
import { BridgeShort } from "./videos/bridge/Bridge";
import { TOTAL } from "./videos/bridge/story";

export const RemotionRoot: React.FC = () => (
  <Composition
    id="BridgeShort"
    component={BridgeShort}
    durationInFrames={TOTAL}
    fps={30}
    width={1080}
    height={1920}
  />
);
