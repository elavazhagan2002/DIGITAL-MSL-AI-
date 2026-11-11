import { useEffect, useMemo, useState } from 'react';
import { useAuth } from './AuthContext';
import { PeoplePage } from './PeoplePage';
import { TherapeuticAreasPage } from './TherapeuticAreasPage';
import { AccountBand } from './settings/AccountBand';
import {
  AuditSection,
  EnvironmentSection,
  IkmSection,
  IntegrationsSection,
  NoticeSection,
  UsersSection,
} from './settings/AdminSections';
import { ActivitySection } from './settings/ActivitySection';
import { ConsentSection, DoctorAreasSection, RegistrationSection } from './settings/DoctorSections';
import { MyAreasSection, WordingSection } from './settings/MslSections';
import { NotificationsSection } from './settings/NotificationsSection';
import { PreferencesSection } from './settings/PreferencesSection';
import { ProfileSection } from './settings/ProfileSection';
import { ScopeSection } from './settings/ScopeSection';
import { SecuritySection } from './settings/SecuritySection';
import { SettingsLayout } from './settings/SettingsLayout';
import { DESCRIPTION, sectionsFor, type SectionId } from './settings/sections';
import { useDoctorMe } from './settings/useDoctorMe';

/** Older deep links stored the admin tab name in sessionStorage; map them onto the sections. */
const LEGACY_ADMIN_TAB: Record<string, SectionId> = { people: 'users', areas: 'adminareas', scope: 'scope', account: 'profile' };

export function SettingsPage() {
  const { user } = useAuth();
  const role = user?.role ?? 'doctor';
  const sections = useMemo(() => sectionsFor(role), [role]);
  const [activeId, setActiveId] = useState<SectionId>(sections[0].id);
  const me = useDoctorMe(role === 'doctor');

  useEffect(() => {
    if (role !== 'admin') return;
    const tab = sessionStorage.getItem('dmsl_admin_settings_tab');
    if (tab && LEGACY_ADMIN_TAB[tab]) {
      setActiveId(LEGACY_ADMIN_TAB[tab]);
      sessionStorage.removeItem('dmsl_admin_settings_tab');
    }
  }, [role]);

  useEffect(() => {
    if (!sections.some((s) => s.id === activeId)) setActiveId(sections[0].id);
  }, [sections, activeId]);

  if (!user) return null;

  const render = () => {
    switch (activeId) {
      case 'profile':
        return <ProfileSection user={user} doctor={me.profile} />;
      case 'security':
        return <SecuritySection user={user} />;
      case 'notifications':
        return <NotificationsSection user={user} />;
      case 'preferences':
        return <PreferencesSection />;
      case 'activity':
        return <ActivitySection user={user} />;
      case 'wording':
        return <WordingSection />;
      case 'myareas':
        return <MyAreasSection user={user} />;
      case 'areas':
        return <DoctorAreasSection user={user} me={me} />;
      case 'consent':
        return <ConsentSection me={me} />;
      case 'registration':
        return <RegistrationSection user={user} me={me} />;
      case 'users':
        return (
          <>
            <UsersSection self={user} />
            <PeoplePage embedded view="teams" />
          </>
        );
      case 'adminareas':
        return <TherapeuticAreasPage />;
      case 'ikm':
        return <IkmSection />;
      case 'notice':
        return <NoticeSection />;
      case 'scope':
        return <ScopeSection />;
      case 'integrations':
        return <IntegrationsSection />;
      case 'audit':
        return <AuditSection />;
      case 'environment':
        return <EnvironmentSection />;
      default:
        return null;
    }
  };

  return (
    <SettingsLayout
      title="Settings"
      description={DESCRIPTION[role]}
      band={<AccountBand user={user} onNavigate={setActiveId} />}
      sections={sections}
      activeId={activeId}
      onSelect={setActiveId}
    >
      {render()}
    </SettingsLayout>
  );
}
