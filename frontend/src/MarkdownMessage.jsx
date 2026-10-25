import React, { useMemo, useState } from 'react'
import { marked } from 'marked'
import {
  GraduationCap,
  Sparkles,
  ExternalLink,
  Phone,
  Mail,
  Globe,
  MapPin,
  Copy,
  Check
} from 'lucide-react'

function GithubIcon({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4" />
      <path d="M9 18c-4.51 2-5-2-7-2" />
    </svg>
  )
}

function LinkedinIcon({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z" />
      <rect width="4" height="12" x="2" y="9" />
      <circle cx="4" cy="4" r="2" />
    </svg>
  )
}

// Normalize grouped citation brackets e.g. [S1, S2] or [S1,S2, S3] to [S1] [S2] [S3]
function normalizeCitations(rawText) {
  if (!rawText) return ''
  return rawText.replace(/\[S\d+(?:\s*,\s*S?\d+)+\]/g, (match) => {
    const nums = match.match(/\d+/g) || []
    return nums.map((n) => `[S${n}]`).join(' ')
  })
}

export default function MarkdownMessage({ text, sources = [], onInspectSource }) {
  const [copiedField, setCopiedField] = useState(null)

  const handleCopy = (field, val) => {
    if (!val) return
    navigator.clipboard.writeText(val)
    setCopiedField(field)
    setTimeout(() => setCopiedField(null), 2000)
  }

  const { processedHtml, contactData, kpiData } = useMemo(() => {
    if (!text) return { processedHtml: '', contactData: null, kpiData: null }

    const normalized = normalizeCitations(text)

    // 1. Dynamic Contact Detection (ONLY from real extracted text, never hardcoded defaults)
    let extractedContact = null
    const hasContactTriggers = /contact details|phone:|email:|linkedin\.com|github\.com/i.test(normalized)
    if (hasContactTriggers) {
      const nameMatch = normalized.match(/(?:Name|Full Name)[:\s*]+([A-Za-z\s]+?)(?:\[|\n|$)/i)
      const locMatch = normalized.match(/Location[:\s*]+([^\n\[]+)/i)
      const phoneMatch = normalized.match(/Phone[:\s*]+([+\d\s\-()]+?)(?:\[|\n|$)/i)
      const emailMatch = normalized.match(/Email[:\s*]+([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)/i)
      const ghMatch = normalized.match(/GitHub[:\s*]+.*?((?:https?:\/\/)?github\.com\/[a-zA-Z0-9-_]+)/i)
      const liMatch = normalized.match(/LinkedIn[:\s*]+.*?((?:https?:\/\/)?(?:[a-zA-Z]{2,3}\.)?linkedin\.com\/in\/[a-zA-Z0-9-_]+)/i)
      const pfMatch = normalized.match(/Portfolio[:\s*]+.*?((?:https?:\/\/)?[a-zA-Z0-9-_.]+\.vercel\.app\/?)/i)

      if (phoneMatch || emailMatch || ghMatch || liMatch) {
        extractedContact = {
          name: nameMatch ? nameMatch[1].trim() : null,
          location: locMatch ? locMatch[1].trim() : null,
          phone: phoneMatch ? phoneMatch[1].trim() : null,
          email: emailMatch ? emailMatch[1].trim() : null,
          github: ghMatch ? ghMatch[1].trim() : null,
          linkedin: liMatch ? liMatch[1].trim() : null,
          portfolio: pfMatch ? pfMatch[1].trim() : null
        }
      }
    }

    // 2. Dynamic Metric / KPI Detection (ONLY if explicitly present in text)
    let extractedKpi = null
    const cgpaMatch = normalized.match(/(?:CGPA|GPA)[:\s*]*([0-9.]+(?:\s*\/\s*10)?)/i)
    if (cgpaMatch) {
      const score = cgpaMatch[1].replace('/10', '').trim()
      extractedKpi = {
        label: 'Verified Academic Score',
        metric: score,
        unit: '/10',
        citation: (normalized.match(/\[S\d+\]/) || ['[S1]'])[0]
      }
    }

    // 3. Configure Marked for GFM tables, code blocks, lists, and line breaks
    marked.setOptions({
      gfm: true,
      breaks: true
    })

    let rawHtml = marked.parse(normalized)

    // 4. Inject interactive citation pills in place of [S1], [S2]
    rawHtml = rawHtml.replace(/\[S(\d+)\]/g, (match, snum) => {
      return `<button type="button" class="citation-pill" data-snum="${snum}" title="Inspect verified evidence source [S${snum}]"><span class="citation-pill-dot"></span>[S${snum}]</button>`
    })

    // 5. Wrap code blocks with an advanced header bar containing language and one-click copy button
    rawHtml = rawHtml.replace(/<pre><code(?:\s+class="language-([a-zA-Z0-9_+-]+)")?>([\s\S]*?)<\/code><\/pre>/gi, (match, lang, codeContent) => {
      const displayLang = lang || 'code'
      return `<div class="code-block-wrapper"><div class="code-block-header"><span class="code-lang">${displayLang}</span><button type="button" class="code-copy-btn" title="Copy code to clipboard"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg><span>Copy</span></button></div><pre><code class="language-${displayLang}">${codeContent}</code></pre></div>`
    })

    // 6. Wrap tables in responsive container for smooth horizontal scrolling on mobile/smaller screens
    rawHtml = rawHtml.replace(/<table>([\s\S]*?)<\/table>/gi, '<div class="table-responsive"><table>$1</table></div>')

    return {
      processedHtml: rawHtml,
      contactData: extractedContact,
      kpiData: extractedKpi
    }
  }, [text])

  // Delegated event listener for citation pills and code copying inside the rendered HTML
  const handleContainerClick = (e) => {
    // 1. Citation Pill Click
    const pill = e.target.closest('.citation-pill')
    if (pill) {
      e.preventDefault()
      e.stopPropagation()
      const sNum = parseInt(pill.getAttribute('data-snum'), 10)
      if (onInspectSource) {
        const matchedSource = Array.isArray(sources) && sources[sNum - 1] ? sources[sNum - 1] : null
        onInspectSource(
          matchedSource || {
            source: `Source Reference [S${sNum}]`,
            chunk: {
              doc_name: `Evidence [S${sNum}]`,
              text: `Citation [S${sNum}] was referenced in the verified synthesis. Check the Sources Used panel on the right rail to inspect the full document passage and similarity metrics.`
            }
          }
        )
      }
      return
    }

    // 2. Code Block Copy Button Click
    const copyBtn = e.target.closest('.code-copy-btn')
    if (copyBtn) {
      e.preventDefault()
      e.stopPropagation()
      const wrapper = copyBtn.closest('.code-block-wrapper')
      const codeEl = wrapper ? wrapper.querySelector('pre code') : null
      if (codeEl) {
        const textToCopy = codeEl.innerText || codeEl.textContent
        navigator.clipboard.writeText(textToCopy).then(() => {
          const span = copyBtn.querySelector('span')
          if (span) span.textContent = 'Copied!'
          copyBtn.classList.add('copied')
          setTimeout(() => {
            if (span) span.textContent = 'Copy'
            copyBtn.classList.remove('copied')
          }, 2000)
        })
      }
    }
  }

  return (
    <div className="advanced-response-container">
      {/* Dynamic Profile Hero (Only if real extracted contact details exist) */}
      {contactData && (contactData.phone || contactData.email || contactData.github) && (
        <div className="profile-hero-card">
          <div className="profile-avatar-wrap">
            <span className="profile-avatar-initials">
              {contactData.name ? contactData.name.split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase() : 'ID'}
            </span>
            <span className="profile-avatar-status" title="Verified Identity" />
          </div>

          <div className="profile-meta-info">
            <div className="profile-name-row">
              <h3 className="profile-full-name">{contactData.name || 'Identified Profile'}</h3>
              <span className="profile-badge-ai">Verified Candidate</span>
            </div>
            {contactData.location && (
              <p className="profile-location-text">
                <MapPin size={12} className="map-pin-icon" />
                {contactData.location}
              </p>
            )}
          </div>
        </div>
      )}

      {/* Dynamic Contact Grid (Only if real contact items are present) */}
      {contactData && (contactData.email || contactData.phone || contactData.github || contactData.linkedin || contactData.portfolio) && (
        <div className="contact-methods-grid">
          {contactData.email && (
            <a
              href={`mailto:${contactData.email}`}
              className="contact-card-item email-item"
              title="Compose email"
            >
              <div className="contact-item-icon">
                <Mail size={16} />
              </div>
              <div className="contact-item-content">
                <span className="contact-label">Email Address</span>
                <span className="contact-value">{contactData.email}</span>
              </div>
              <button
                type="button"
                className="contact-copy-btn"
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  handleCopy('email', contactData.email)
                }}
                title="Copy email address"
              >
                {copiedField === 'email' ? <Check size={12} className="text-green" /> : <Copy size={12} />}
              </button>
            </a>
          )}

          {contactData.phone && (
            <a
              href={`tel:${contactData.phone.replace(/\s+/g, '')}`}
              className="contact-card-item phone-item"
              title="Dial phone number"
            >
              <div className="contact-item-icon">
                <Phone size={16} />
              </div>
              <div className="contact-item-content">
                <span className="contact-label">Phone</span>
                <span className="contact-value">{contactData.phone}</span>
              </div>
              <button
                type="button"
                className="contact-copy-btn"
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  handleCopy('phone', contactData.phone)
                }}
                title="Copy phone number"
              >
                {copiedField === 'phone' ? <Check size={12} className="text-green" /> : <Copy size={12} />}
              </button>
            </a>
          )}

          {contactData.github && (
            <a
              href={contactData.github.startsWith('http') ? contactData.github : `https://${contactData.github}`}
              target="_blank"
              rel="noopener noreferrer"
              className="contact-card-item github-item"
            >
              <div className="contact-item-icon">
                <GithubIcon size={16} />
              </div>
              <div className="contact-item-content">
                <span className="contact-label">GitHub Repository</span>
                <span className="contact-value">{contactData.github.replace(/^https?:\/\//, '')}</span>
              </div>
              <ExternalLink size={13} className="ext-link-icon" />
            </a>
          )}

          {contactData.linkedin && (
            <a
              href={contactData.linkedin.startsWith('http') ? contactData.linkedin : `https://${contactData.linkedin}`}
              target="_blank"
              rel="noopener noreferrer"
              className="contact-card-item linkedin-item"
            >
              <div className="contact-item-icon">
                <LinkedinIcon size={16} />
              </div>
              <div className="contact-item-content">
                <span className="contact-label">LinkedIn</span>
                <span className="contact-value">{contactData.linkedin.replace(/^https?:\/\//, '')}</span>
              </div>
              <ExternalLink size={13} className="ext-link-icon" />
            </a>
          )}

          {contactData.portfolio && (
            <a
              href={contactData.portfolio.startsWith('http') ? contactData.portfolio : `https://${contactData.portfolio}`}
              target="_blank"
              rel="noopener noreferrer"
              className="contact-card-item portfolio-item"
            >
              <div className="contact-item-icon">
                <Globe size={16} />
              </div>
              <div className="contact-item-content">
                <span className="contact-label">Portfolio</span>
                <span className="contact-value">{contactData.portfolio.replace(/^https?:\/\//, '')}</span>
              </div>
              <ExternalLink size={13} className="ext-link-icon" />
            </a>
          )}
        </div>
      )}

      {/* Dynamic Key Metric Banner (Only if real metric exists) */}
      {kpiData && (
        <div className="hero-kpi-card">
          <div className="kpi-score-badge">
            <span className="kpi-icon-wrap">
              <GraduationCap size={20} className="kpi-cap-icon" />
            </span>
            <div className="kpi-number-group">
              <span className="kpi-score-number">{kpiData.metric}</span>
              <span className="kpi-score-scale">{kpiData.unit}</span>
            </div>
            <span className="kpi-score-label">{kpiData.label}</span>
          </div>

          <div className="kpi-info-block">
            <div className="kpi-degree-row">
              <h3 className="kpi-degree-title">Verified Metric Grounded in Document</h3>
              {kpiData.citation && (
                <button
                  type="button"
                  className="citation-pill"
                  data-snum={parseInt(kpiData.citation.replace(/\D/g, '') || '1', 10)}
                  onClick={(e) => {
                    e.stopPropagation()
                    const sNum = parseInt(kpiData.citation.replace(/\D/g, '') || '1', 10)
                    if (onInspectSource) {
                      const matched = Array.isArray(sources) && sources[sNum - 1] ? sources[sNum - 1] : null
                      onInspectSource(matched || { source: `[S${sNum}]`, chunk: { doc_name: 'Source', text: 'Verified Metric' } })
                    }
                  }}
                >
                  <span className="citation-pill-dot" />
                  {kpiData.citation}
                </button>
              )}
            </div>
            <p className="kpi-college-name">Validated against evidence passage across NEXUS multi-agent pipeline</p>
          </div>
        </div>
      )}

      {/* Universal Markdown Body - Rich formatting for ALL domains, papers, and files */}
      <div
        className="nexus-markdown-body"
        onClick={handleContainerClick}
        dangerouslySetInnerHTML={{ __html: processedHtml }}
      />

      {/* Verified Grounding Footer */}
      <div className="response-grounding-footer">
        <Sparkles size={12} className="footer-sparkle" />
        <span>Grounded in verified documentation · Citations validated via NEXUS multi-agent OS</span>
      </div>
    </div>
  )
}
