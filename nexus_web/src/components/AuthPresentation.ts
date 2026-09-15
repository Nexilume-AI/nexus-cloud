import { t, useLocale } from "../localization";
import { useApplicationDistribution } from '../app/distribution';
import { personalAuthPresentation, validateAuthPresentation } from '../app/authPresentation';

export function useAuthPresentation() {
  useLocale();
  const selected = useApplicationDistribution().authPresentation;
  if (selected !== undefined) {
    validateAuthPresentation(selected);
    return localizePresentation(selected);
  }
  return localizePresentation(personalAuthPresentation);
}

function localizePresentation(copy: typeof personalAuthPresentation): typeof personalAuthPresentation {
  return {
    ...copy,
    introduction: t(copy.introduction),
    assurances: [t(copy.assurances[0]), t(copy.assurances[1]), t(copy.assurances[2])],
    oauthDescription: t(copy.oauthDescription), dismissLabel: t(copy.dismissLabel),
    reason: provided => t(copy.reason(provided)), protectedPrivacy: t(copy.protectedPrivacy),
    computerIntroduction: t(copy.computerIntroduction), computerTerminal: t(copy.computerTerminal),
    computerPrivacy: t(copy.computerPrivacy),
  };
}
