import React, { useEffect, useRef } from 'react';
import { View, Animated, StyleSheet } from 'react-native';

interface AudioVisualizerProps {
  isPlaying: boolean;
  barCount?: number;
  color?: string;
  height?: number;
}

export const AudioVisualizer: React.FC<AudioVisualizerProps> = ({
  isPlaying,
  barCount = 20,
  color = '#FF6321',
  height = 40,
}) => {
  const animatedValues = useRef<Animated.Value[]>(
    Array.from({ length: barCount }, () => new Animated.Value(0.15))
  ).current;

  useEffect(() => {
    let animations: Animated.CompositeAnimation[] = [];

    if (isPlaying) {
      animations = animatedValues.map((anim, index) => {
        const randomHeight = Math.random() * 0.75 + 0.25;
        const duration = 280 + (index % 5) * 80;
        return Animated.loop(
          Animated.sequence([
            Animated.timing(anim, {
              toValue: randomHeight,
              duration: duration,
              useNativeDriver: false,
            }),
            Animated.timing(anim, {
              toValue: 0.15,
              duration: duration,
              useNativeDriver: false,
            }),
          ])
        );
      });
      Animated.parallel(animations).start();
    } else {
      animatedValues.forEach((anim) => {
        Animated.timing(anim, {
          toValue: 0.15,
          duration: 200,
          useNativeDriver: false,
        }).start();
      });
    }

    return () => {
      animations.forEach((a) => a.stop());
    };
  }, [isPlaying, animatedValues]);

  return (
    <View style={[styles.container, { height }]}>
      {animatedValues.map((anim, index) => (
        <Animated.View
          key={index}
          style={[
            styles.bar,
            {
              backgroundColor: color,
              height: anim.interpolate({
                inputRange: [0, 1],
                outputRange: [height * 0.12, height],
              }),
            },
          ]}
        />
      ))}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 3,
    paddingHorizontal: 12,
  },
  bar: {
    width: 3.5,
    borderRadius: 2,
  },
});
