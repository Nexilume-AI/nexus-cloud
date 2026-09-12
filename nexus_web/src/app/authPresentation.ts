/** Build-selected explanatory copy only; never an authentication policy. */
export type AuthPresentation = {
  introduction: string;
  assurances: readonly [string, string, string];
  oauthDescription: string;
  dismissLabel: string;
  reason: (provided: string) => string;
  protectedPrivacy: string;
  computerIntroduction: string;
  computerTerminal: string;
  computerPrivacy: string;
};

export const personalAuthPresentation: AuthPresentation = {
  introduction: 'This is your personal Nexus installation. Sign in with the owner account to access your Agents, models, files and connected devices.',
  assurances: ['One installation, one owner', 'Sign-in protects your private data', 'Devices execute only within authorized scopes'],
  oauthDescription: 'Use the sign-in method configured for this installation.',
  dismissLabel: 'Not now',
  reason: () => 'Sign in with the owner account created when you set up this Nexus installation.',
  protectedPrivacy: 'Your private data is not loaded before sign-in.',
  computerIntroduction: 'Pair Nexus Computer Runtime with this installation, then use its terminal, Workspace and Agent tools. The Runtime connects outward; no inbound SSH connection is needed.',
  computerTerminal: 'Open and resume terminal sessions on your paired Computer.',
  computerPrivacy: 'Computer details, credentials and sessions are not loaded before sign-in.',
};

export function validateAuthPresentation(value: AuthPresentation) {
  if (!value || typeof value.reason !== 'function'
      || !Array.isArray(value.assurances) || value.assurances.length !== 3
      || ![value.introduction, ...value.assurances, value.oauthDescription,
        value.dismissLabel, value.protectedPrivacy, value.computerIntroduction,
        value.computerTerminal, value.computerPrivacy].every(text => typeof text === 'string' && text.trim())) {
    throw new Error('Authentication presentation is incomplete.');
  }
}
