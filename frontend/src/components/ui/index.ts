// Design-system primitive library. Compose screens from these — never
// hand-roll a button/input/select/checkbox/modal/panel/badge/chip/segmented
// control or empty/loading/error state. See the gallery at /?gallery=1 and
// .claude/rfcs/phase3/design-system-audit.md.

export { Button } from './Button'
export type { ButtonProps, ButtonVariant, ButtonSize } from './Button'

export { Modal } from './Modal'
export type { ModalProps, ModalSize } from './Modal'

export { ConfirmDialog } from './ConfirmDialog'
export type { ConfirmDialogData } from './ConfirmDialog'

export { FormField } from './FormField'
export type { FormFieldProps } from './FormField'

export { TextField } from './TextField'
export type { TextFieldProps } from './TextField'

export { DateField } from './DateField'
export type { DateFieldProps } from './DateField'

export { PasswordField } from './PasswordField'
export type { PasswordFieldProps } from './PasswordField'

export { useSecretReveal } from './useSecretReveal'
export type { SecretReveal, SecretRevealState, UseSecretRevealOptions } from './useSecretReveal'

export { NumberField } from './NumberField'
export type { NumberFieldProps } from './NumberField'

export { MoneyField } from './MoneyField'
export type { MoneyFieldProps } from './MoneyField'

export { TextArea } from './TextArea'
export type { TextAreaProps } from './TextArea'

export { Select } from './Select'
export type { SelectProps, SelectOption, SelectOptGroup, SelectOptionOrGroup } from './Select'

export { ModelPicker, modelRefOf, bareModelId, groupModels } from './ModelPicker'
export type { ModelPickerProps } from './ModelPicker'

export { Checkbox } from './Checkbox'
export type { CheckboxProps } from './Checkbox'

export { CheckboxGroup } from './CheckboxGroup'
export type { CheckboxGroupProps, CheckboxGroupOption } from './CheckboxGroup'

export { RadioGroup } from './RadioGroup'
export type { RadioGroupProps, RadioGroupOption } from './RadioGroup'

export { Panel } from './Panel'
export type { PanelProps } from './Panel'

export { SummaryBar, StatGrid } from './SummaryBar'
export type { SummaryBarProps, StatCard, StatTone } from './SummaryBar'

export { Tag } from './Tag'
export type { TagProps, TagTone } from './Tag'

export { TagInput } from './TagInput'
export type { TagInputProps } from './TagInput'

export { Badge } from './Badge'
export type { BadgeProps, BadgeTone } from './Badge'

export { SegmentedControl } from './SegmentedControl'
export type { SegmentedControlProps, SegmentedOption } from './SegmentedControl'

export { PageTabs } from './PageTabs'
export type { PageTabsProps, PageTab } from './PageTabs'

export { CollapsibleSection } from './CollapsibleSection'
export type { CollapsibleSectionProps } from './CollapsibleSection'

export { EmptyState, Loading, ErrorState } from './States'
export type { EmptyStateProps, LoadingProps, ErrorStateProps } from './States'

export { Skeleton } from './Skeleton'
export { useSkeletonHandoff } from './skeletonHandoff'
export type { SkeletonProps } from './Skeleton'

export { TabSkeleton } from './TabSkeleton'
export type { TabSkeletonProps, TabSkeletonVariant } from './TabSkeleton'

// ── Layout + typography foundation (phase-3 §1–§4) ──
export { BP } from './breakpoints'
export type { Breakpoint } from './breakpoints'

export type { SpaceToken } from './tokens'

export { Container } from './Container'
export type { ContainerProps, ContainerSize } from './Container'

export { Grid } from './Grid'
export type { GridProps } from './Grid'

export { Stack } from './Stack'
export { List } from './List'
export type { ListProps } from './List'
export type { StackProps, StackAlign } from './Stack'

export { SwipeActions } from './SwipeActions'
export { useSwipeRows } from './useSwipeRows'
export type { SwipeAction } from './SwipeActions'

export { Cluster } from './Cluster'
export type { ClusterProps, ClusterAlign, ClusterJustify } from './Cluster'

export { PageHeader } from './PageHeader'
export type { PageHeaderProps } from './PageHeader'

export { Text } from './Text'
export type { TextProps, TextSize, TextTone, TextWeight, TextElement } from './Text'

export { Money } from './Money'
export type { MoneyProps } from './Money'

export { Switch } from './Switch'
export type { SwitchProps } from './Switch'

export { InfoPopover } from './InfoPopover'
export type { InfoPopoverProps } from './InfoPopover'

export { StatInfo } from './StatInfo'
export type { StatInfoProps } from './StatInfo'

export { ActionMenu } from './ActionMenu'
export type { ActionMenuProps, ActionMenuItem } from './ActionMenu'

export { Meter } from './Meter'
export type { MeterProps, MeterVariant, MeterSize, MeterTone, MeterSegment } from './Meter'
export { PermissionModeControl, type PermissionMode } from './PermissionModeControl'
