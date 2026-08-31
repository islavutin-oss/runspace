'use client'

import { useState, useRef, useEffect, KeyboardEvent, DragEvent, ClipboardEvent } from 'react'
import { Send, Bold, Italic, Code, AtSign, Mic, Square, Paperclip, X } from 'lucide-react'

const MAX_FILE_SIZE = 10 * 1024 * 1024  // 10MB
const ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']

interface FileAttachment {
  name: string
  size: number
  type: string
  url: string
}

interface Agent {
  id: string
  name: string
  avatar: string
  color: string
}

interface User {
  id: string
  name: string
  avatar?: string
  color?: string
  email?: string
}

interface MessageComposerProps {
  placeholder?: string
  agents?: Agent[]
  users?: User[]   // humans on the same tenant — surfaced in @-mention autocomplete alongside agents
  onSend: (text: string, images?: string[], attachments?: FileAttachment[]) => void
  onSendAudio?: (blob: Blob, duration: number) => void
  disabled?: boolean
  draftKey?: string  // localStorage key for draft persistence
}

function validateFile(file: File): string | null {
  if (file.size > MAX_FILE_SIZE) return 'File too large (max 10MB)'
  return null
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

export default function MessageComposer({ placeholder = 'Message…', agents = [], users = [], onSend, onSendAudio, disabled, draftKey }: MessageComposerProps) {
  const [text, setText] = useState('')
  const [images, setImages] = useState<string[]>([])
  const [attachments, setAttachments] = useState<FileAttachment[]>([])
  const [showMentions, setShowMentions] = useState(false)
  const [mentionFilter, setMentionFilter] = useState('')
  const [recording, setRecording] = useState(false)
  const [recordTime, setRecordTime] = useState(0)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const mediaRecorder = useRef<MediaRecorder | null>(null)
  const audioChunks = useRef<Blob[]>([])
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const recordTimeRef = useRef(0)

  // Restore draft from localStorage
  useEffect(() => {
    if (!draftKey) return
    const saved = localStorage.getItem(`draft:${draftKey}`)
    if (saved) setText(saved)
  }, [draftKey])

  // Save draft to localStorage (debounced)
  useEffect(() => {
    if (!draftKey) return
    const timeout = setTimeout(() => {
      if (text.trim()) {
        localStorage.setItem(`draft:${draftKey}`, text)
      } else {
        localStorage.removeItem(`draft:${draftKey}`)
      }
    }, 300)
    return () => clearTimeout(timeout)
  }, [text, draftKey])

  useEffect(() => { inputRef.current?.focus() }, [])

  async function addFiles(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      if (validateFile(file)) continue
      if (images.length + attachments.length >= 10) break

      const dataUrl = await readFileAsDataUrl(file)

      if (ALLOWED_IMAGE_TYPES.includes(file.type)) {
        setImages(prev => [...prev, dataUrl])
      } else {
        setAttachments(prev => [...prev, {
          name: file.name, size: file.size, type: file.type, url: dataUrl,
        }])
      }
    }
  }

  function removeImage(index: number) {
    setImages(prev => prev.filter((_, i) => i !== index))
  }

  function removeAttachment(index: number) {
    setAttachments(prev => prev.filter((_, i) => i !== index))
  }

  // Paste handler — images from clipboard
  function handlePaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    const items = e.clipboardData?.items
    if (!items) return
    const imageFiles: File[] = []
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.startsWith('image/')) {
        const file = items[i].getAsFile()
        if (file) imageFiles.push(file)
      }
    }
    if (imageFiles.length > 0) {
      e.preventDefault()
      addFiles(imageFiles)
    }
  }

  // Drag and drop
  function handleDragOver(e: DragEvent) {
    e.preventDefault()
    setDragging(true)
  }

  function handleDragLeave(e: DragEvent) {
    e.preventDefault()
    setDragging(false)
  }

  function handleDrop(e: DragEvent) {
    e.preventDefault()
    setDragging(false)
    if (e.dataTransfer?.files) {
      addFiles(e.dataTransfer.files)
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
    if (e.key === 'b' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      wrapSelection('**')
    }
    if (e.key === 'i' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      wrapSelection('*')
    }
  }

  function wrapSelection(marker: string) {
    const el = inputRef.current
    if (!el) return
    const start = el.selectionStart
    const end = el.selectionEnd
    const selected = text.slice(start, end)
    const newText = text.slice(0, start) + marker + selected + marker + text.slice(end)
    setText(newText)
    setTimeout(() => {
      el.focus()
      el.setSelectionRange(start + marker.length, end + marker.length)
    }, 0)
  }

  function handleSend() {
    if ((!text.trim() && images.length === 0 && attachments.length === 0) || disabled) return
    onSend(
      text.trim(),
      images.length > 0 ? images : undefined,
      attachments.length > 0 ? attachments : undefined,
    )
    setText('')
    setImages([])
    setAttachments([])
    setShowMentions(false)
    if (draftKey) localStorage.removeItem(`draft:${draftKey}`)
  }

  function handleInput(value: string) {
    setText(value)
    const lastAt = value.lastIndexOf('@')
    if (lastAt >= 0 && (lastAt === 0 || value[lastAt - 1] === ' ')) {
      const query = value.slice(lastAt + 1).split(' ')[0].toLowerCase()
      setMentionFilter(query)
      setShowMentions(true)
    } else {
      setShowMentions(false)
    }
  }

  function insertMention(agent: Agent) {
    const lastAt = text.lastIndexOf('@')
    const before = text.slice(0, lastAt)
    const after = text.slice(lastAt).replace(/@\S*/, '')
    setText(`${before}@${agent.name} ${after}`)
    setShowMentions(false)
    inputRef.current?.focus()
  }

  async function toggleRecording() {
    if (recording) {
      mediaRecorder.current?.stop()
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      audioChunks.current = []
      const mr = new MediaRecorder(stream)
      mediaRecorder.current = mr
      mr.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.current.push(e.data) }
      mr.onstop = () => {
        stream.getTracks().forEach(t => t.stop())
        if (timerRef.current) clearInterval(timerRef.current)
        const blob = new Blob(audioChunks.current, { type: 'audio/webm' })
        if (blob.size > 0) onSendAudio?.(blob, recordTimeRef.current)
        setRecording(false)
        setRecordTime(0)
        recordTimeRef.current = 0
      }
      mr.start(250)  // emit data frequently to minimize initial capture delay
      setRecording(true)
      setRecordTime(0)
      recordTimeRef.current = 0
      timerRef.current = setInterval(() => {
        recordTimeRef.current += 1
        setRecordTime(recordTimeRef.current)
      }, 1000)
    } catch {}
  }

  function formatTime(sec: number) {
    return `${Math.floor(sec / 60)}:${(sec % 60).toString().padStart(2, '0')}`
  }

  const filteredAgents = agents.filter(a =>
    a.name.toLowerCase().includes(mentionFilter) || a.id.toLowerCase().includes(mentionFilter)
  )
  const filteredUsers = users.filter(u =>
    u.name.toLowerCase().includes(mentionFilter)
    || u.id.toLowerCase().includes(mentionFilter)
    || (u.email || '').toLowerCase().includes(mentionFilter)
  )
  const totalMentionables = filteredAgents.length + filteredUsers.length

  const hasContent = text.trim() || images.length > 0 || attachments.length > 0

  return (
    <div className="relative"
      onDragOver={handleDragOver} onDragLeave={handleDragLeave} onDrop={handleDrop}>

      {/* Drag overlay */}
      {dragging && (
        <div className="absolute inset-0 z-20 bg-blue-50/90 border-2 border-dashed border-blue-400 rounded-lg flex items-center justify-center pointer-events-none">
          <span className="text-sm text-blue-600 font-medium">Drop files here</span>
        </div>
      )}

      {/* @mention autocomplete — agents + humans */}
      {showMentions && totalMentionables > 0 && (
        <div className="absolute bottom-full left-0 mb-1 w-72 bg-white border border-gray-200 rounded-lg shadow-lg overflow-hidden z-10 max-h-80 overflow-y-auto">
          {filteredAgents.length > 0 && (
            <>
              <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-gray-400 bg-gray-50">Agents</div>
              {filteredAgents.map(a => (
                <button key={`agent-${a.id}`} onClick={() => insertMention(a)}
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-gray-50 text-left">
                  <span className="text-base">{a.avatar}</span>
                  <span className="font-medium">{a.name}</span>
                  <span className="text-xs text-gray-400 ml-auto" style={{ color: a.color }}>@{a.name}</span>
                </button>
              ))}
            </>
          )}
          {filteredUsers.length > 0 && (
            <>
              <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-gray-400 bg-gray-50 border-t border-gray-100">People</div>
              {filteredUsers.map(u => (
                <button key={`user-${u.id}`} onClick={() => insertMention({ id: u.id, name: u.name, avatar: u.avatar || '👤', color: u.color || '#6B7280' })}
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-gray-50 text-left">
                  <span className="text-base">{u.avatar || '👤'}</span>
                  <div className="flex flex-col">
                    <span className="font-medium">{u.name}</span>
                    {u.email && <span className="text-[10px] text-gray-400">{u.email}</span>}
                  </div>
                  <span className="text-xs text-gray-400 ml-auto">@{u.name}</span>
                </button>
              ))}
            </>
          )}
        </div>
      )}

      <div className="border border-gray-400/50 rounded-lg focus-within:border-gray-500 focus-within:shadow-[0_0_0_1px_rgba(0,0,0,0.1)] transition-all bg-white">
        {/* Image previews */}
        {images.length > 0 && (
          <div className="flex gap-2 px-3 pt-2 pb-1 overflow-x-auto">
            {images.map((src, i) => (
              <div key={i} className="relative shrink-0 group/img">
                <img src={src} alt={`Upload ${i + 1}`}
                  className="w-16 h-16 object-cover rounded-lg border border-gray-200" />
                <button onClick={() => removeImage(i)}
                  className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-gray-800 text-white rounded-full flex items-center justify-center opacity-0 group-hover/img:opacity-100 transition-opacity">
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* File attachment previews */}
        {attachments.length > 0 && (
          <div className="flex flex-col gap-1 px-3 pt-2 pb-1">
            {attachments.map((file, i) => (
              <div key={i} className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-gray-50 border border-gray-200 group/file">
                <span className="text-sm shrink-0">
                  {file.type.includes('pdf') ? '📄' : file.type.includes('spreadsheet') || file.type.includes('csv') ? '📊' : file.type.includes('word') || file.type.includes('document') ? '📝' : file.type.includes('presentation') ? '📊' : '📎'}
                </span>
                <span className="text-xs text-gray-700 truncate flex-1">{file.name}</span>
                <span className="text-[10px] text-gray-400 shrink-0">
                  {file.size < 1024 ? `${file.size}B` : file.size < 1024 * 1024 ? `${(file.size / 1024).toFixed(0)}KB` : `${(file.size / (1024 * 1024)).toFixed(1)}MB`}
                </span>
                <button onClick={() => removeAttachment(i)}
                  className="text-gray-400 hover:text-gray-600 opacity-0 group-hover/file:opacity-100 transition-opacity">
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Text input area */}
        {recording ? (
          <div className="flex items-center gap-3 px-3 py-3">
            <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            <span className="text-sm text-red-600 font-medium">{formatTime(recordTime)}</span>
            <span className="text-xs text-gray-400">Recording… tap mic to send</span>
            <div className="flex-1" />
          </div>
        ) : (
          <textarea
            ref={inputRef}
            value={text}
            onChange={e => handleInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={placeholder}
            rows={1}
            className="w-full px-3 py-2.5 text-[13px] leading-[1.46] outline-none bg-transparent resize-none min-h-[38px] max-h-[160px]"
            style={{ height: 'auto', overflow: 'hidden' }}
            onInput={e => {
              const el = e.currentTarget
              el.style.height = 'auto'
              el.style.height = Math.min(el.scrollHeight, 160) + 'px'
            }}
          />
        )}

        {/* Toolbar — below text area, like Slack */}
        <div className="flex items-center gap-0.5 px-1.5 py-1 border-t border-gray-100">
          <button onClick={() => wrapSelection('**')} className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600" title="Bold (Ctrl+B)">
            <Bold className="h-4 w-4" />
          </button>
          <button onClick={() => wrapSelection('*')} className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600" title="Italic (Ctrl+I)">
            <Italic className="h-4 w-4" />
          </button>
          <button onClick={() => wrapSelection('`')} className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600" title="Code">
            <Code className="h-4 w-4" />
          </button>
          <div className="w-px h-4 bg-gray-200 mx-0.5" />
          <button onClick={() => fileInputRef.current?.click()}
            className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600" title="Attach file">
            <Paperclip className="h-4 w-4" />
          </button>
          <input ref={fileInputRef} type="file" multiple className="hidden"
            onChange={e => { if (e.target.files) addFiles(e.target.files); e.target.value = '' }} />
          {agents.length > 0 && (
            <button onClick={() => { setText(text + '@'); setShowMentions(true); setMentionFilter(''); inputRef.current?.focus() }}
              className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600" title="Mention @agent">
              <AtSign className="h-4 w-4" />
            </button>
          )}
          {onSendAudio && (
            <button onClick={toggleRecording}
              className={`p-1.5 rounded transition-colors ${
                recording
                  ? 'bg-red-600 text-white hover:bg-red-700'
                  : hasContent
                    ? 'hidden'
                    : 'text-gray-400 hover:text-gray-600 hover:bg-gray-100'
              }`}
              title={recording ? 'Stop & send' : 'Record audio message'}>
              {recording ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
            </button>
          )}
          <div className="flex-1" />
          <button
            onClick={handleSend}
            disabled={disabled || !hasContent}
            className={`p-1.5 rounded-lg transition-colors ${
              hasContent && !disabled
                ? 'bg-[#007a5a] text-white hover:bg-[#005e45]'
                : 'text-gray-300 cursor-default'
            }`}
            title="Send message"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
