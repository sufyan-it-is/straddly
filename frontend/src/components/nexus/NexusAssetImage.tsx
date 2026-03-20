import React, { useState } from 'react';

type NexusAssetImageProps = {
  src: string;
  fallbackSrc?: string;
  alt: string;
  className?: string;
  style?: React.CSSProperties;
};

const NexusAssetImage: React.FC<NexusAssetImageProps> = ({
  src,
  fallbackSrc,
  alt,
  className,
  style,
}) => {
  const [currentSrc, setCurrentSrc] = useState(src);

  return (
    <img
      src={currentSrc}
      alt={alt}
      className={className}
      style={style}
      onError={() => {
        if (fallbackSrc && currentSrc !== fallbackSrc) {
          setCurrentSrc(fallbackSrc);
        }
      }}
    />
  );
};

export default NexusAssetImage;
