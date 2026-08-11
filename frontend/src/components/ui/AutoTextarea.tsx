import { useLayoutEffect, useRef, type TextareaHTMLAttributes } from 'react'

export function AutoTextarea({ onInput, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const ref = useRef<HTMLTextAreaElement | null>(null)

  const resize = () => {
    const element = ref.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${element.scrollHeight}px`
  }

  useLayoutEffect(resize, [props.defaultValue, props.value])

  return (
    <textarea
      {...props}
      ref={ref}
      onInput={(event) => {
        resize()
        onInput?.(event)
      }}
    />
  )
}
