import React from "react";
import { PublicShell } from "../components/common/PublicShell";

/**
 * Terms and Conditions — a DRAFT for legal review, not a finished legal document.
 *
 * Bracketed placeholders ([LIKE THIS]) mark facts the codebase does not establish:
 * the operating entity, its address, governing law, and named officers. They must
 * be filled in by the business before publication. Do not replace them with
 * invented values.
 */

export const TERMS_PATH = "/terms";
export const TERMS_LAST_UPDATED = "27 October 2026";

const Section: React.FC<{ id: string; title: string; children: React.ReactNode }> = ({ id, title, children }) => (
  <section aria-labelledby={id} className="space-y-3">
    <h2 id={id} className="text-base font-semibold text-fg">
      {title}
    </h2>
    <div className="space-y-3 text-sm leading-relaxed text-mist-300">{children}</div>
  </section>
);

const Placeholder: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span className="rounded-sm bg-raised px-1 text-danger">[{children}]</span>
);

export const TermsPage: React.FC = () => {
  return (
    <PublicShell contentId="terms-content" skipLabel="Skip to terms">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold text-fg">Terms and Conditions</h1>
        <p className="text-xs tabular-nums text-fg-subtle">Last updated: {TERMS_LAST_UPDATED}</p>
        <p className="rounded-md border border-line-strong bg-panel p-3 text-xs leading-relaxed text-fg-muted">
          Draft for legal review. Items in <Placeholder>square brackets</Placeholder> are not yet established and must
          be completed before publication. This page is not a guarantee of legal compliance.
        </p>
      </div>

      <Section id="t-intro" title="1. Agreement">
        <p>
          These Terms govern your use of the EchoFlow web application and related services (the “Service”), operated
          by <Placeholder>OPERATING ENTITY NAME</Placeholder>. By creating an account or using the Service you agree to
          these Terms. If you do not agree, do not use the Service.
        </p>
      </Section>

      <Section id="t-eligibility" title="2. Eligibility">
        <p>
          Sign-up asks for your date of birth. If you are under 18, the sign-up form requires a parent or guardian
          email address, and the Service may only be used with that person’s involvement.{" "}
          <Placeholder>MINIMUM AGE AND PARENTAL CONSENT RULES — confirm with counsel for each jurisdiction</Placeholder>{" "}
          You may not use the Service if you are barred from receiving it under applicable law.
        </p>
      </Section>

      <Section id="t-accounts" title="3. Your account">
        <p>
          You choose a username and password when you register. You are responsible for keeping your credentials
          confidential and for all activity under your account. Tell us promptly if you believe your account has been
          accessed without permission. Usernames must not impersonate another person or organisation.
        </p>
      </Section>

      <Section id="t-content" title="4. Content you create and upload">
        <p>
          EchoFlow lets you upload audio clips, add titles, tags and cover images, comment on clips, and share clips
          with other users. You keep ownership of what you upload. You grant EchoFlow a non-exclusive, worldwide,
          royalty-free licence to host, process, encode, store, stream and display that content so the Service can
          operate, including making it available to other users as you direct. You confirm that you have the rights
          needed to grant that licence.
        </p>
        <p>
          Uploads are subject to the size, duration and daily upload limits shown in the app, which depend on your
          plan. Those limits may change; the current values shown in the app apply.
        </p>
      </Section>

      <Section id="t-acceptable-use" title="5. Acceptable use">
        <p>You must not use the Service to:</p>
        <ul className="list-disc space-y-1.5 pl-5">
          <li>upload content you do not have the right to share, including material that infringes copyright or other rights;</li>
          <li>post sexual content involving minors, or any content that exploits or endangers children;</li>
          <li>post content that threatens, harasses, or incites violence against any person or group;</li>
          <li>impersonate another person, or misrepresent your identity or affiliation;</li>
          <li>send spam, run automated scripts or bots against the Service, or circumvent rate or upload limits;</li>
          <li>attempt to access accounts, data or systems that are not yours, or interfere with the Service’s security;</li>
          <li>use the Service in breach of any applicable law.</li>
        </ul>
      </Section>

      <Section id="t-moderation" title="6. Moderation, reports and removal">
        <p>
          Any user can report a clip. We may review reported content and remove or restrict content, or suspend
          accounts, that we reasonably believe breaches these Terms or applicable law. Rights holders and others may
          request removal of content through the notice procedure described in our grievance information. Our
          responses to notices follow the process set out in <Placeholder>GRIEVANCE POLICY LINK</Placeholder>.
        </p>
      </Section>

      <Section id="t-ip" title="7. Intellectual property">
        <p>
          The EchoFlow name, logo, interface, software and design are owned by{" "}
          <Placeholder>OPERATING ENTITY NAME</Placeholder> or its licensors and are protected by law. These Terms do
          not grant you any right to use them except as needed to use the Service. Content created by other users
          belongs to those users and may be used only as the Service allows.
        </p>
      </Section>

      <Section id="t-subscriptions" title="8. Paid plans">
        <p>
          Some features are available on a paid plan. Prices, billing periods, renewal terms, refund rules and how to
          cancel are shown at purchase and in your account. Where paid features are purchased through an app store or
          other platform, that platform’s terms also apply. <Placeholder>REFUND AND CANCELLATION POLICY — to be drafted</Placeholder>
        </p>
      </Section>

      <Section id="t-availability" title="9. Availability of the Service">
        <p>
          We aim to keep the Service available but do not promise uninterrupted or error-free operation. We may
          change, suspend or discontinue features, and we may perform maintenance. Some features depend on
          third-party services and may be affected by their outages.
        </p>
      </Section>

      <Section id="t-termination" title="10. Suspension and termination">
        <p>
          You may stop using the Service at any time and request deletion of your account. We may suspend or terminate
          your access if you breach these Terms, if required by law, or if continued access would create risk to the
          Service, other users or third parties. Where practical we will tell you why.
        </p>
      </Section>

      <Section id="t-privacy" title="11. Personal data">
        <p>
          How we collect and use personal data is described in our Privacy Policy{" "}
          <Placeholder>PRIVACY POLICY LINK — not yet published</Placeholder>. You can request access to or erasure of
          your personal data through your account or the contact details below, subject to any legal retention
          obligations.
        </p>
      </Section>

      <Section id="t-disclaimers" title="12. Disclaimers">
        <p>
          The Service is provided “as is” and “as available”, without warranties of any kind to the extent permitted by
          law. We do not guarantee the accuracy, quality or lawfulness of content posted by users, including news or
          educational material.
        </p>
      </Section>

      <Section id="t-liability" title="13. Limitation of liability">
        <p>
          To the maximum extent permitted by law, EchoFlow and its operators are not liable for indirect, incidental,
          special or consequential losses, or for loss of data or profits, arising from the Service.{" "}
          <Placeholder>LIABILITY CAP AND CARVE-OUTS — to be set with counsel</Placeholder> Nothing in these Terms limits
          liability that cannot be limited under applicable law.
        </p>
      </Section>

      <Section id="t-indemnity" title="14. Indemnity">
        <p>
          <Placeholder>INDEMNITY CLAUSE — to be reviewed by counsel</Placeholder>
        </p>
      </Section>

      <Section id="t-changes" title="15. Changes to these Terms">
        <p>
          We may update these Terms. When we do, we will change the “Last updated” date above and, for material
          changes, notify you in the app <Placeholder>NOTICE PERIOD AND METHOD — to be confirmed</Placeholder>. Continued
          use after the effective date means you accept the updated Terms.
        </p>
      </Section>

      <Section id="t-law" title="16. Governing law">
        <p>
          These Terms are governed by the laws of <Placeholder>GOVERNING LAW</Placeholder>, and disputes are subject to
          the jurisdiction of <Placeholder>COURTS / ARBITRATION SEAT</Placeholder>.
        </p>
      </Section>

      <Section id="t-contact" title="17. Contact">
        <p>
          Operator: <Placeholder>OPERATING ENTITY NAME AND REGISTERED ADDRESS</Placeholder>
          <br />
          General contact: <Placeholder>SUPPORT EMAIL</Placeholder>
          <br />
          Grievance officer: <Placeholder>NAME AND EMAIL — required for notices under local rules</Placeholder>
        <br />
        To report a problem or send a takedown request now, use the{" "}
        <a href="/support" className="text-link hover:underline">support and grievance page</a>.
        </p>
      </Section>
    </PublicShell>
  );
};

export default TermsPage;
