import type { SVGProps } from 'react'

type Props = Omit<SVGProps<SVGSVGElement>, 'width' | 'height'> & {
  size?: number | string
}

/** Official Model Context Protocol mark, adapted to inherit the UI color. */
export function McpIcon({ size = 18, ...props }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 180 180"
      fill="none"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      <path
        d="M18 84.8528 85.8822 16.9706c9.3726-9.3726 24.5688-9.3726 33.9408 0 9.373 9.3725 9.373 24.5685 0 33.9411L68.5581 102.177"
        stroke="currentColor"
        strokeWidth="12"
        strokeLinecap="round"
      />
      <path
        d="m69.2652 101.47 50.5578-50.5583c9.373-9.3726 24.569-9.3726 33.942 0l.353.3535c9.373 9.3726 9.373 24.5686 0 33.9411L92.7248 146.6c-3.1242 3.124-3.1242 8.189 0 11.313l12.6062 12.607"
        stroke="currentColor"
        strokeWidth="12"
        strokeLinecap="round"
      />
      <path
        d="M102.853 33.9411 52.6482 84.1457c-9.3726 9.3726-9.3726 24.5683 0 33.9413 9.3726 9.372 24.5685 9.372 33.9411 0L136.794 67.8822"
        stroke="currentColor"
        strokeWidth="12"
        strokeLinecap="round"
      />
    </svg>
  )
}
