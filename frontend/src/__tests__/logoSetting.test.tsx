/**
 * Tests for the branding LogoSetting control (prompts-045) in the Application
 * configuration tab.
 *
 * The API client is mocked so upload/delete mutations and the logo-info query
 * are driven deterministically. Client-side type/size validation is exercised
 * without touching the network.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      ...actual.api,
      getLogoInfo: vi.fn(),
      uploadLogo: vi.fn(),
      deleteLogo: vi.fn(),
    },
  }
})

import { api } from '../api/client'
import { LogoSetting } from '../pages/Configuration'

function renderWithClient() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <LogoSetting />
    </QueryClientProvider>,
  )
}

function makeFile(name: string, type: string, size: number): File {
  const file = new File(['x'], name, { type })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('LogoSetting (prompts-045)', () => {
  it('rejects an unsupported image type before upload', async () => {
    vi.mocked(api.getLogoInfo).mockResolvedValue({ has_logo: false })
    renderWithClient()

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(fileInput, {
      target: { files: [makeFile('logo.svg', 'image/svg+xml', 1000)] },
    })

    expect(await screen.findByText(/unsupported image type/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /upload/i })).toBeDisabled()
    expect(api.uploadLogo).not.toHaveBeenCalled()
  })

  it('rejects an image exceeding the 2 MB limit', async () => {
    vi.mocked(api.getLogoInfo).mockResolvedValue({ has_logo: false })
    renderWithClient()

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(fileInput, {
      target: { files: [makeFile('big.png', 'image/png', 3 * 1024 * 1024)] },
    })

    expect(await screen.findByText(/exceeds the 2 mb limit/i)).toBeInTheDocument()
    expect(api.uploadLogo).not.toHaveBeenCalled()
  })

  it('uploads a valid image', async () => {
    vi.mocked(api.getLogoInfo).mockResolvedValue({ has_logo: false })
    vi.mocked(api.uploadLogo).mockResolvedValue({ logo_path: 'data/branding/logo.png', has_logo: true })
    renderWithClient()

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    const good = makeFile('logo.png', 'image/png', 5000)
    fireEvent.change(fileInput, { target: { files: [good] } })

    const uploadBtn = screen.getByRole('button', { name: /upload/i })
    expect(uploadBtn).not.toBeDisabled()
    fireEvent.click(uploadBtn)

    await waitFor(() => expect(api.uploadLogo).toHaveBeenCalledWith(good))
  })

  it('offers a remove control and deletes the existing logo', async () => {
    vi.mocked(api.getLogoInfo).mockResolvedValue({ has_logo: true })
    vi.mocked(api.deleteLogo).mockResolvedValue({ has_logo: false })
    renderWithClient()

    const removeBtn = await screen.findByRole('button', { name: /remove/i })
    fireEvent.click(removeBtn)

    await waitFor(() => expect(api.deleteLogo).toHaveBeenCalledTimes(1))
  })

  it('hides the remove control when no logo is configured', async () => {
    vi.mocked(api.getLogoInfo).mockResolvedValue({ has_logo: false })
    renderWithClient()

    await waitFor(() => expect(api.getLogoInfo).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /remove/i })).toBeNull()
  })
})
