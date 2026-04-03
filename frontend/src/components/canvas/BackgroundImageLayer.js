import { useEffect, useState } from "react";

export function useBackgroundImage(backgroundImageUrl) {
  const [image, setImage] = useState(null);

  useEffect(() => {
    if (!backgroundImageUrl) {
      setImage(null);
      return undefined;
    }

    const nextImage = new Image();
    nextImage.decoding = "async";
    nextImage.onload = () => setImage(nextImage);
    nextImage.onerror = () => setImage(null);
    nextImage.src = backgroundImageUrl;

    return () => {
      setImage(null);
    };
  }, [backgroundImageUrl]);

  return image;
}

export function drawBackgroundImage(ctx, image, frameWidth, frameHeight) {
  if (!ctx || !image) return;
  ctx.drawImage(image, 0, 0, frameWidth, frameHeight);
}
