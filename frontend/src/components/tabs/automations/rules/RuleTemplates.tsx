// "Start from a template" for Rules, in the Lucius gallery's own cards.
import { useState } from 'react'
import { Briefcase, Coffee, House, Pencil, Receipt, Repeat, Split, Wallet } from 'lucide-react'
import { RULE_TEMPLATES } from './ruleTemplateList'
import type { RuleForm } from './ruleModel'

const ICONS: Record<string, typeof Pencil> = {
  'rename-payee': Pencil,
  subscription: Repeat,
  salary: Wallet,
  'fixed-bill': House,
  'shared-purchase': Split,
  coffee: Coffee,
  fees: Receipt,
  'work-card': Briefcase,
}

export function RuleTemplates({ onTemplate }: { onTemplate: (seed: RuleForm) => void }) {
  const [showAll, setShowAll] = useState(false)
  const templates = showAll ? RULE_TEMPLATES : RULE_TEMPLATES.slice(0, 4)
  return (
    <section className="a3-tpl-section" aria-labelledby="rule-templates-heading">
      <div className="a3-sec-label" id="rule-templates-heading">
        Start from a template
        <span className="rule" />
      </div>
      <div className="a3-tpl-grid">
        {templates.map((template) => {
          const Icon = ICONS[template.key] ?? Pencil
          return (
            <button
              key={template.key}
              type="button"
              className="a3-tpl"
              aria-label={`${template.title} template`}
              onClick={() => onTemplate(template.seed())}
            >
              <span className="a3-tpl-ic">
                <Icon size={15} strokeWidth={1.7} aria-hidden="true" />
              </span>
              <span className="a3-tpl-body">
                <span className="a3-tpl-title">{template.title}</span>
                <span className="a3-tpl-blurb">{template.blurb}</span>
              </span>
            </button>
          )
        })}
      </div>
      <button type="button" className="a3-linkbtn" onClick={() => setShowAll((value) => !value)}>
        {showAll ? 'Show fewer templates' : 'Browse all templates'}
      </button>
    </section>
  )
}
