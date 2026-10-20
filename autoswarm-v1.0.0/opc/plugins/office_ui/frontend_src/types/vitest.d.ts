declare module 'vitest' {
  export function describe(name: string, fn: () => void): void
  export function it(name: string, fn: () => void): void
  export const expect: {
    (value: unknown): {
      toBe(expected: unknown): void
      toEqual(expected: unknown): void
    }
  }
}
