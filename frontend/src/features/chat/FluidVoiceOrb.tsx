import { useEffect, useRef } from 'react'

export type VoiceState = 'ready' | 'listening' | 'thinking' | 'speaking'

interface Props {
  state: VoiceState
  stream: MediaStream | null
  onClick: () => void
}

const SIZE = 640
const CENTER = SIZE / 2

export function FluidVoiceOrb({ state, stream, onClick }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const context = canvas?.getContext('2d')
    if (!canvas || !context) return

    let animationFrame = 0
    let audioContext: AudioContext | null = null
    let analyser: AnalyserNode | null = null
    let samples: Uint8Array<ArrayBuffer> | null = null
    let level = 0
    let time = 0

    if (state === 'listening' && stream) {
      audioContext = new AudioContext()
      analyser = audioContext.createAnalyser()
      analyser.fftSize = 256
      analyser.smoothingTimeConstant = 0.78
      samples = new Uint8Array(analyser.frequencyBinCount)
      audioContext.createMediaStreamSource(stream).connect(analyser)
      void audioContext.resume()
    }

    const readLevel = () => {
      if (!analyser || !samples) return 0
      analyser.getByteTimeDomainData(samples)
      let sum = 0
      for (const value of samples) {
        const normalized = (value - 128) / 128
        sum += normalized * normalized
      }
      return Math.min(1, Math.sqrt(sum / samples.length) * 4)
    }

    const tracePath = (clock: number, radius: number, amplitude: number, phase: number) => {
      context.beginPath()
      const points = 120
      for (let index = 0; index <= points; index += 1) {
        const angle = (index / points) * Math.PI * 2
        const edge =
          radius +
          amplitude *
            (Math.sin(angle * 2 + clock + phase) +
              0.45 * Math.sin(angle * 3 - clock * 0.8 + phase) +
              0.22 * Math.sin(angle * 4 + clock * 1.3))
        const x = CENTER + Math.cos(angle) * edge
        const y = CENTER + Math.sin(angle) * edge
        if (index === 0) context.moveTo(x, y)
        else context.lineTo(x, y)
      }
      context.closePath()
    }

    const strokeBlob = (
      clock: number,
      radius: number,
      amplitude: number,
      phase: number,
      color: string,
      width: number,
      glow: number,
    ) => {
      tracePath(clock, radius, amplitude, phase)
      context.lineWidth = width
      context.strokeStyle = color
      context.shadowColor = color
      context.shadowBlur = glow
      context.stroke()
      context.shadowBlur = 0
    }

    const draw = () => {
      const liveLevel = readLevel()
      let target = 0.1
      if (state === 'listening') target = 0.3 + liveLevel * 1.55
      else if (state === 'speaking') target = 0.42 + 0.12 * Math.sin(time * 5.2)
      else if (state === 'thinking') target = 0.26 + 0.1 * Math.sin(time * 4)

      level += (target - level) * 0.1
      const speed = state === 'thinking' ? 2.4 : 1 + level * 1.4
      time += 0.016 * speed

      context.clearRect(0, 0, SIZE, SIZE)
      const amplitude = 5 + level * 34
      const radius = 168 + level * 24
      const width = 2 + level * 7
      const glow = 10 + level * 58

      context.globalCompositeOperation = 'source-over'
      context.save()
      tracePath(time, radius, amplitude * 0.95, 0)
      context.clip()
      const lightX = CENTER - radius * 0.4
      const lightY = CENTER - radius * 0.46
      const body = context.createRadialGradient(
        lightX,
        lightY,
        radius * 0.06,
        CENTER,
        CENTER,
        radius * 1.25,
      )
      body.addColorStop(0, 'rgba(218, 255, 234, 0.20)')
      body.addColorStop(0.4, 'rgba(89, 201, 142, 0.08)')
      body.addColorStop(0.78, 'rgba(38, 112, 73, 0.03)')
      body.addColorStop(1, 'rgba(7, 18, 12, 0)')
      context.fillStyle = body
      context.fillRect(0, 0, SIZE, SIZE)

      const highlight = context.createRadialGradient(
        lightX,
        lightY,
        0,
        lightX,
        lightY,
        radius * 0.5,
      )
      highlight.addColorStop(0, `rgba(255, 255, 255, ${0.09 + level * 0.12})`)
      highlight.addColorStop(1, 'rgba(255, 255, 255, 0)')
      context.fillStyle = highlight
      context.fillRect(0, 0, SIZE, SIZE)
      context.restore()

      context.globalCompositeOperation = 'lighter'
      strokeBlob(time, radius, amplitude * 0.95, 0, 'rgba(89, 201, 142, 0.95)', width, glow)
      strokeBlob(
        time * 1.18 + 1,
        radius - 12,
        amplitude,
        2.1,
        'rgba(38, 128, 82, 0.88)',
        width * 0.85,
        glow * 0.85,
      )
      strokeBlob(
        time * 0.86 + 2,
        radius + 10,
        amplitude * 0.85,
        4.2,
        'rgba(137, 230, 180, 0.82)',
        width * 0.7,
        glow * 0.7,
      )
      context.globalCompositeOperation = 'source-over'
      animationFrame = requestAnimationFrame(draw)
    }

    draw()
    return () => {
      cancelAnimationFrame(animationFrame)
      if (audioContext) void audioContext.close()
    }
  }, [state, stream])

  return (
    <button
      className={`fluid-orb-button fluid-orb-button--${state}`}
      type="button"
      onClick={onClick}
      disabled={state === 'thinking' || state === 'speaking'}
      aria-label={state === 'listening' ? 'Stop recording' : 'Start voice input'}
    >
      <canvas ref={canvasRef} width={SIZE} height={SIZE} />
    </button>
  )
}
