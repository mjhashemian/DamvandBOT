import * as React from 'react'
import { cn } from '../../lib/utils'

function Sidebar({ className, ...props }: React.ComponentProps<'aside'>) { return <aside data-slot="sidebar" className={cn('ui-sidebar', className)} {...props} /> }
function SidebarHeader({ className, ...props }: React.ComponentProps<'div'>) { return <div data-slot="sidebar-header" className={cn('ui-sidebar-header', className)} {...props} /> }
function SidebarContent({ className, ...props }: React.ComponentProps<'nav'>) { return <nav data-slot="sidebar-content" className={cn('ui-sidebar-content', className)} {...props} /> }
function SidebarFooter({ className, ...props }: React.ComponentProps<'div'>) { return <div data-slot="sidebar-footer" className={cn('ui-sidebar-footer', className)} {...props} /> }
function SidebarMenuButton({ className, active, ...props }: React.ComponentProps<'a'> & { active?: boolean }) { return <a data-slot="sidebar-menu-button" aria-current={active ? 'page' : undefined} className={cn('ui-sidebar-menu-button', active && 'active', className)} {...props} /> }
export { Sidebar, SidebarHeader, SidebarContent, SidebarFooter, SidebarMenuButton }
