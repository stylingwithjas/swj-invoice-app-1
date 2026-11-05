# Universal luxury redesign — preview review

Branch: `codex/universal-luxury-mobile`. PR #90. Preview only; no merge authorization.

## Audit and implementation

The app is standalone HTML/JavaScript with per-page CSS and existing shared Home theme tokens. No new framework or dependencies were added. The approved Home composition is unchanged. Shared workspace styles use its actual palette and fonts.

- Navigation retains Home, Invoice, Finance, Board, More. Existing route destinations and handlers remain.
- Invoice and Luxury: existing fields and accordions retained; total dock measures navigation height and leaves 12px clearance, with combined content padding.
- Finance and Employee Pay: shared surfaces, readable tabs, quieter actions and financial typography; revenue takes full width on phones. Every existing metric and tab remains.
- Jobs: vertical mobile project list, actual status labels/counts, existing attention labels and amounts. Card opens the original editor. Desktop Kanban and optional mobile Kanban remain.
- Editor: existing input nodes remain mounted inside accessible accordions. Secondary sections start collapsed on phones; collapsed summaries refresh for each project. Save/Cancel stays visible in a sticky footer. Dialog focus returns to the invoking control.
- More: grouped links, close control and focus containment, retaining each page's existing destinations.
- Shopping, tax, archive, photography, hours, cash and internal utilities: shared token and responsive surface treatment. Client-facing contract/signature pages are not redesigned.

## Verification

- 242 existing Python tests passed with socket connections and DNS calls blocked.
- Existing inline scripts match baseline on every edited page except documented Board UI rendering/error-state hooks.
- Browser fixture checks confirmed five links and 12px dock clearance at 320/375/390/430 widths. Invoice also checked at 768/1024/1440.
- Other internal screens checked at 320/390/768/1024/1440 with no page-level horizontal overflow in sample states.
- Unsaved address survived accordion collapse/reopen. More, mobile cards, Kanban toggle and Employee Pay inspected.
- Syntax validation passed after the final job-editor collapse/focus adjustment. Follow-up browser validation was blocked by browser URL policy; no workaround attempted. Screenshots predate that final interaction adjustment.

## Limits and owner review

Screenshots use isolated sample records and include before views from e401edb. Some tools show empty states or access configuration; populated tables and every modal were not exhaustively checked. Real iPhone keyboard/safe-area behavior and authenticated write/payment/signature/send flows remain unverified. Existing operational inline colors and icons remain in some controls and should receive visual review.

No API/lib/SQL/payment/PDF/contract calculation code changed. No stored records or schema were changed. No client communications or live payments were sent during QA. No known controls were removed, but full live workflow parity is not claimed. Do not merge or deploy to production without owner approval.
