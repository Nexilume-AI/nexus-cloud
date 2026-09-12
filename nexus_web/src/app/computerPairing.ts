/** Build-selected guidance only. Computer authority is enforced by the Server. */
export type ComputerPairingPresentation = {
  introduction: string;
  workspaceDescription: string;
  connectedDescription: (name: string) => string;
};

export const personalComputerPairing: ComputerPairingPresentation = {
  introduction: 'Install Nexus Computer Runtime as your current user. It connects outward to your Nexus instance; no SSH host, password, or inbound port is needed.',
  workspaceDescription: 'Agents can only access paths inside this folder and within their authorized scopes.',
  connectedDescription: name => `${name} is ready to attach to your Agents in Private Display.`,
};

export function validateComputerPairing(value: ComputerPairingPresentation) {
  if (!value || ![value.introduction,value.workspaceDescription].every(text => typeof text==='string' && text.trim())
      || typeof value.connectedDescription !== 'function') {
    throw new Error('Computer pairing presentation is incomplete.');
  }
}
