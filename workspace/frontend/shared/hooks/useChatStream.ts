'use client'

/**
 * Shared chat stream logic — used by both GeneralChannel and AgentChat.
 * Files are uploaded to /upload first, then referenced by file_id in chat request.
 * No base64 in JSON — standard multipart upload.
 */

interface StreamAgent {
  id: string
  name: string
  avatar: string
  color: string
}

interface StreamAttachment {
  name: string
  type: string
  size: number
  url: string  // data URL from FileReader
}

interface StreamCallbacks {
  onToolCall?: (name: string) => void
  onTranscription?: (text: string) => void
  onResponse: (text: string, toolsUsed: string[], attachments?: any[]) => void
  onError: (message: string) => void
}

/** Upload a file to the server. Returns file_id. */
async function uploadFile(apiBase: string, file: Blob, filename: string): Promise<string> {
  const formData = new FormData()
  formData.append('file', file, filename)
  const res = await fetch(`${apiBase}/upload`, { method: 'POST', body: formData })
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`)
  const data = await res.json()
  return data.file_id
}

/** Upload multiple attachments + images. Returns file_ids. */
async function uploadAttachments(
  apiBase: string,
  images?: string[],
  attachments?: StreamAttachment[],
): Promise<string[]> {
  const fileIds: string[] = []

  // Upload file attachments
  for (const att of attachments || []) {
    if (!att.url) continue
    const res = await fetch(att.url)
    const blob = await res.blob()
    const id = await uploadFile(apiBase, blob, att.name)
    fileIds.push(id)
  }

  // Upload images
  for (let i = 0; i < (images || []).length; i++) {
    const src = images![i]
    const res = await fetch(src)
    const blob = await res.blob()
    const id = await uploadFile(apiBase, blob, `image-${i + 1}.png`)
    fileIds.push(id)
  }

  return fileIds
}

export async function chatStream(
  apiBase: string,
  agent: StreamAgent,
  message: string,
  sessionId: string,
  callbacks: StreamCallbacks,
  options?: {
    images?: string[]
    attachments?: StreamAttachment[]
    audioBlob?: Blob  // raw audio blob — uploaded as file, no base64
    senderName?: string  // real user name from JWT
  },
) {
  const body: Record<string, any> = {
    app_id: agent.id,
    message,
    session_id: sessionId,
  }
  if (options?.senderName) body.sender_name = options.senderName

  // Upload attachments + images → get file_ids
  if (options?.attachments?.length || options?.images?.length) {
    try {
      body.file_ids = await uploadAttachments(apiBase, options.images, options.attachments)
    } catch (e) {
      callbacks.onError('Failed to upload files')
      return
    }
  }

  // Upload audio blob → get file_id
  if (options?.audioBlob) {
    try {
      const audioFileId = await uploadFile(apiBase, options.audioBlob, 'voice.webm')
      body.file_ids = [...(body.file_ids || []), audioFileId]
    } catch (e) {
      callbacks.onError('Failed to upload voice message')
      return
    }
  }

  // Report what actually went wrong.
  //
  let res: Response
  try {
    res = await fetch(`${apiBase}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch (e) {
    // Genuinely unreachable: DNS, refused connection, CORS, offline.
    callbacks.onError(
      `Could not reach the server (${e instanceof Error ? e.message : String(e)}).`,
    )
    return
  }

  if (!res.ok) {
    // Server answered — surface its status and whatever it said. The body
    // is usually FastAPI's {"detail": "..."} and names the real problem.
    let detail = ''
    try {
      const text = (await res.text()).trim()
      if (text) {
        try {
          const j = JSON.parse(text)
          detail = typeof j?.detail === 'string' ? j.detail : text
        } catch {
          detail = text
        }
      }
    } catch {
      /* body already consumed or unreadable — status alone still helps */
    }
    callbacks.onError(
      `Server error ${res.status}${detail ? `: ${detail.slice(0, 300)}` : ''}`,
    )
    return
  }

  if (!res.body) {
    callbacks.onError('Server returned an empty response body.')
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let gotResponse = false
  let lastToolCallAt = 0

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try {
        const event = JSON.parse(line.slice(6))
        if (event.type === 'tool_call') {
          lastToolCallAt = Date.now()
          callbacks.onToolCall?.(event.name)
        } else if (event.type === 'transcription') {
          callbacks.onTranscription?.(event.text)
        } else if (event.type === 'response') {
          // Ensure tool_call indicator is visible for at least 400ms before response clears it
          const elapsed = Date.now() - lastToolCallAt
          if (lastToolCallAt > 0 && elapsed < 400) {
            await new Promise(r => setTimeout(r, 400 - elapsed))
          }
          gotResponse = true
          callbacks.onResponse(event.text, event.tools_used || [], event.attachments)
        }
      } catch {}
    }
  }

  // Stream ended without a response — backend error
  if (!gotResponse) {
    callbacks.onError('Agent did not respond. Please try again.')
  }
}

export type { StreamAgent, StreamAttachment, StreamCallbacks }
